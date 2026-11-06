"""MCP 工具的具体实现。所有函数返回可 JSON 序列化的 dict。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import builder, lint, runner, splitter_compat, truth, upgrade
from .circ import Circuit, Project, load_project
from .geometry import is_sequential, ports_of
from .nets import build_netlist

SERVER_VERSION = "1.2.1"


class ToolError(RuntimeError):
    pass


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


def _project(path: str) -> Project:
    p = _resolve(path)
    try:
        return load_project(p)
    except (FileNotFoundError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def _runtime() -> runner.Runtime:
    try:
        return runner.find_runtime(os.environ.get("LOGISIM_EXE"))
    except runner.LogisimNotFound as exc:
        raise ToolError(str(exc)) from exc


def _circuit(project: Project, name: Optional[str]) -> Circuit:
    try:
        return project.circuit(name)
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


def _prepare(
    path: str, circuit: Optional[str]
) -> Tuple[Project, Circuit, Circuit, Optional[Dict[str, Dict[str, object]]]]:
    """加载电路；若含 Splitter，则生成无头兼容副本用于仿真。

    返回 (仿真用工程, 仿真用电路, 原始电路, 派生列描述或 None)。"""
    project = _project(path)
    c = _circuit(project, circuit)
    if not splitter_compat.needs_compat(c):
        return project, c, c, None
    try:
        tmpdir = Path(tempfile.mkdtemp(prefix="lse_compat_"))
        new_path, buses = splitter_compat.transform(project, c, tmpdir)
        derived = splitter_compat.derived_spec(buses)
    except splitter_compat.CompatError as exc:
        raise ToolError(f"Splitter 无头兼容变换失败: {exc}") from exc
    sim_project = load_project(new_path)
    sim_c = sim_project.circuit(c.name)
    return sim_project, sim_c, c, derived


# ---------------------------------------------------------------------------
# 环境与结构
# ---------------------------------------------------------------------------


def logisim_info() -> Dict[str, object]:
    info: Dict[str, object] = {"server_version": SERVER_VERSION, "test_vector_engine": True}
    try:
        rt = _runtime()
        info.update(
            {
                "found": True,
                "runtime": rt.as_dict(),
                "version": runner.version_of(rt),
                "engine": "logisim-evolution --test-vector (headless)",
            }
        )
    except ToolError as exc:
        info.update({"found": False, "error": str(exc), "hint": "安装 Logisim-evolution 或设置 LOGISIM_EXE"})
    info["cwd"] = str(Path.cwd())
    return info


def circuit_list(path: str) -> Dict[str, object]:
    project = _project(path)
    circuits = []
    for c in project.circuits.values():
        circuits.append(
            {
                "name": c.name,
                "components": len(c.comps),
                "wires": len(c.wires),
                "inputs": [
                    {"label": p.label, "width": p.width}
                    for p in sorted(c.input_pins(), key=lambda x: x.label)
                    if p.label
                ],
                "outputs": [
                    {"label": p.label, "width": p.width}
                    for p in sorted(c.output_pins(), key=lambda x: x.label)
                    if p.label
                ],
            }
        )
    return {
        "file": str(project.path),
        "source_version": project.source,
        "main": project.main,
        "circuit_count": len(circuits),
        "circuits": circuits,
    }


def circuit_inspect(
    path: str,
    circuit: Optional[str] = None,
    include_nets: bool = False,
    include_ports: bool = True,
) -> Dict[str, object]:
    project = _project(path)
    c = _circuit(project, circuit)
    netlist = build_netlist(c)
    comps = []
    for comp in c.comps:
        entry: Dict[str, object] = {
            "name": comp.name,
            "loc": list(comp.loc),
            "lib": comp.lib,
            "label": comp.label or None,
            "attrs": comp.attrs,
        }
        if include_ports:
            ports = ports_of(comp)
            if ports:
                entry["ports"] = [
                    {"hint": p.hint, "loc": list(p.loc), "direction": p.direction, "width": p.width}
                    for p in ports
                ]
        comps.append(entry)

    result: Dict[str, object] = {
        "file": str(project.path),
        "circuit": c.name,
        "is_main": c.name == project.main,
        "project": {
            "source_version": project.source,
            "libs": project.lib_desc,
            "main": project.main,
            "circuits": list(project.circuits),
        },
        "stats": {
            "components": len(c.comps),
            "wires": len(c.wires),
            "nets": len(netlist.nets),
            "pins": len(c.pins()),
        },
        "inventory": c.components_by_name(),
        "pins": {
            "inputs": [
                {"label": p.label, "width": p.width, "loc": list(p.loc)}
                for p in sorted(c.input_pins(), key=lambda x: x.label)
            ],
            "outputs": [
                {"label": p.label, "width": p.width, "loc": list(p.loc)}
                for p in sorted(c.output_pins(), key=lambda x: x.label)
            ],
        },
        "components": comps,
        "subcircuits": [x for x in project.circuits if x != c.name],
        "unsupported_geometry": sorted({x.name for x in netlist.unsupported}),
    }
    if include_nets:
        result["nets"] = [n.as_dict() for n in netlist.nets[:80]]
        if len(netlist.nets) > 80:
            result["nets_truncated"] = len(netlist.nets) - 80
    return result


# ---------------------------------------------------------------------------
# 仿真
# ---------------------------------------------------------------------------


def _assignment_rows(
    ins: List[truth.PinInfo],
    payload: Dict[str, object],
    steps: Optional[List[Dict[str, object]]],
) -> List[Dict[str, object]]:
    if steps:
        rows = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ToolError(f"steps[{i}] 必须是对象")
            rows.append(step)
        return rows
    if not isinstance(payload, dict) or not payload:
        raise ToolError("需要提供 inputs（端口名到取值的映射）或 steps")
    return [payload]


def circuit_simulate(
    path: str,
    circuit: Optional[str] = None,
    inputs: Optional[Dict[str, object]] = None,
    steps: Optional[List[Dict[str, object]]] = None,
    clock: Optional[str] = None,
) -> Dict[str, object]:
    sim_project, sim_c, orig_c, derived = _prepare(path, circuit)
    rt = _runtime()

    ins_all, outs, skipped = truth.signal_pins(sim_c)
    user_labels = {p.label for p in truth.signal_pins(orig_c)[0]}
    ins = [p for p in ins_all if p.label in user_labels]
    if not ins:
        raise ToolError("电路没有带标签的输入引脚")
    known = {p.label for p in ins}
    payloads = _assignment_rows(ins, inputs or {}, steps)
    for i, row in enumerate(payloads):
        missing = [p.label for p in ins if p.label not in row]
        extra = [k for k in row if k not in known]
        if missing:
            raise ToolError(f"第 {i + 1} 组输入缺少 {missing}；可用输入: {sorted(known)}")
        if extra:
            raise ToolError(f"第 {i + 1} 组输入包含未知端口 {extra}；可用输入: {sorted(known)}")

    if steps:
        text = _sequential_vector(ins, ins_all, outs, steps, derived)
        res = runner.run_vector_file(rt, sim_project.path, sim_c.name, text, timeout=180)
        if not res.ok:
            raise ToolError(res.error or "仿真失败")
        decoded = []
        for i, step in enumerate(steps, start=1):
            decoded.append(
                {
                    "step": i,
                    "in": {
                        p.label: (int(step[p.label]) if isinstance(step[p.label], bool) else step[p.label])
                        for p in ins
                    },
                    "out": {p.label: _read(res, i, p) for p in outs},
                }
            )
        result = {
            "circuit": sim_c.name,
            "mode": "sequential",
            "clock": clock or "auto (每个 step 自动打一拍)",
            "steps": decoded,
            "output_format": "十进制整数；U/E/x 表示未知或冲突",
            "skipped_pins": skipped,
        }
        if derived:
            result["note"] = "电路含 Splitter，已用等价兼容副本完成无头仿真（GUI 中 Splitter 正常）"
        return result

    outputs, _, _, _ = truth.evaluate_rows(rt, sim_project, sim_c, payloads, timeout=180, derived=derived)
    row = payloads[0]
    result = {
        "circuit": sim_c.name,
        "mode": "combinational",
        "inputs": {p.label: int(row[p.label]) if isinstance(row[p.label], (int, bool)) else row[p.label] for p in ins},
        "outputs": outputs[0],
        "skipped_pins": skipped,
    }
    if derived:
        result["note"] = "电路含 Splitter，已用等价兼容副本完成无头仿真（GUI 中 Splitter 正常）"
    return result


def _read(res: runner.RunResult, row: int, pin: truth.PinInfo):
    raw = res.value(row, pin.label, "0" * pin.width if pin.width > 1 else "0")
    return truth._decode(raw, pin.width)


def _sequential_vector(
    ins_user: List[truth.PinInfo],
    ins_all: List[truth.PinInfo],
    outs: List[truth.PinInfo],
    steps: List[Dict[str, object]],
    derived: Optional[Dict[str, Dict[str, object]]] = None,
) -> str:
    header = truth._header(ins_all, outs) + ["<set>", "<seq>"]
    lines = [" ".join(header)]
    for i, step in enumerate(steps, start=1):
        user_vals = {p.label: step[p.label] for p in ins_user}
        dv = splitter_compat.derived_values(derived, user_vals) if derived else {}
        vals = []
        for p in ins_all:
            if p.label in dv:
                vals.append(runner.format_value(dv[p.label], p.width))
                continue
            v = step[p.label]
            vals.append(runner.format_value(int(v), p.width) if isinstance(v, (int, bool)) else str(v))
        for p in outs:
            vals.append("0" * p.width if p.width > 1 else "0")
        vals += ["1", str(i)]
        lines.append(" ".join(vals))
    return "\n".join(lines) + "\n"


def circuit_truth_table(
    path: str, circuit: Optional[str] = None, max_rows: int = 1024, include_rows: bool = True
) -> Dict[str, object]:
    sim_project, sim_c, orig_c, derived = _prepare(path, circuit)
    rt = _runtime()

    ins, outs, skipped = truth.signal_pins(sim_c)
    netlist = build_netlist(orig_c)

    if any(is_sequential(x) for x in orig_c.comps):
        raise ToolError("电路包含时序元件，真值表枚举不适用；请使用 circuit_simulate 的 steps 参数")

    try:
        result = truth.truth_table(rt, sim_project, sim_c, max_rows=max_rows, derived=derived)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except RuntimeError as exc:
        raise ToolError(str(exc)) from exc
    if not include_rows:
        result.pop("rows", None)
    result["skipped_pins"] = skipped
    result["unsupported_geometry"] = sorted({x.name for x in netlist.unsupported})
    if derived:
        result["note"] = "电路含 Splitter，真值表经等价兼容副本验证（GUI 中 Splitter 正常）"
    return result


def circuit_test(
    path: str,
    circuit: Optional[str] = None,
    vectors: Optional[str] = None,
    vector_file: Optional[str] = None,
    max_report: int = 40,
) -> Dict[str, object]:
    project = _project(path)
    c = _circuit(project, circuit)
    rt = _runtime()

    if vector_file:
        vf = _resolve(vector_file)
        if not vf.exists():
            raise ToolError(f"找不到测试向量文件: {vf}")
        text = vf.read_text(encoding="utf-8")
    elif vectors:
        text = vectors
    else:
        raise ToolError("需要 vectors（向量文本）或 vector_file（文件路径）")

    header, expected = _parse_vectors(text)
    if not header:
        raise ToolError("测试向量为空或缺少表头")

    sim_project, sim_c, orig_c, derived = _prepare(path, circuit)
    if derived:
        text = _augment_vectors(header, expected, derived)
        header, expected = _parse_vectors(text)

    res = runner.run_vector_file(rt, sim_project.path, sim_c.name, text, timeout=180)
    if not res.ok and not res.rows:
        raise ToolError(res.error or "Logisim 无法运行该测试向量")

    names = [h.split("[")[0] for h in header]
    details = []
    failures = 0
    for i, exp in enumerate(expected, start=1):
        row_actual: Dict[str, object] = {}
        row_ok = True
        for name, exp_val in zip(names, exp):
            if exp_val.upper() == "<DC>":
                continue
            printed = res.rows.get(i, {}).get(name)
            if printed is None:
                actual = exp_val
            else:
                actual = printed
            row_actual[name] = actual
            if printed is not None and not _same_value(exp_val, printed):
                row_ok = False
        if not row_ok:
            failures += 1
        if len(details) < max_report:
            details.append({"row": i, "expected": dict(zip(names, exp)), "actual": row_actual, "ok": row_ok})

    total = len(expected)
    return {
        "circuit": c.name,
        "total": total,
        "passed": total - failures,
        "failed": failures,
        "all_passed": failures == 0,
        "rows": details,
        "truncated": max(0, total - len(details)),
        "error": None if res.ok else res.error,
    }


def _parse_vectors(text: str):
    header: List[str] = []
    rows: List[List[str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("#") else ""
        if not line:
            continue
        parts = line.split()
        if not header:
            header = parts
        else:
            rows.append(parts)
    return header, rows


def _augment_vectors(
    header: List[str],
    rows: List[List[str]],
    derived: Dict[str, Dict[str, object]],
) -> str:
    """把派生列（Splitter 兼容）追加到测试向量文本中。"""
    names = [h.split("[")[0] for h in header]
    extra = []
    for col, d in derived.items():
        w = int(d["width"])
        extra.append((f"{col}[{w}]" if w > 1 else col, col))
    lines = [" ".join(list(header) + [tok for tok, _ in extra])]
    for parts in rows:
        if len(parts) != len(names):
            raise ToolError(f"向量行列数与表头不符: {parts}")
        vals = {n: v for n, v in zip(names, parts)}
        try:
            dv = splitter_compat.derived_values(derived, vals)
        except splitter_compat.CompatError as exc:
            raise ToolError(str(exc)) from exc
        lines.append(
            " ".join(list(parts) + [runner.format_value(dv[col], int(derived[col]["width"])) for _, col in extra])
        )
    return "\n".join(lines) + "\n"


def _same_value(expected: str, actual: str) -> bool:
    e, a = expected.strip().lower(), actual.strip().lower()
    if e == a:
        return True
    if e == "<float>":
        return a in ("u", "x", "e")
    try:
        return int(e, 2) == int(a, 2)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 生成 / 渲染 / 检查
# ---------------------------------------------------------------------------


def circuit_create(
    output: str,
    netlist: Dict[str, object] | str,
    smoke_test: bool = True,
) -> Dict[str, object]:
    if isinstance(netlist, str):
        try:
            netlist = json.loads(netlist)
        except json.JSONDecodeError as exc:
            raise ToolError(f"netlist 不是合法 JSON: {exc}") from exc
    if not isinstance(netlist, dict):
        raise ToolError("netlist 必须是对象")

    out = _resolve(output)
    if out.suffix.lower() != ".circ":
        out = out.with_suffix(".circ")
    try:
        stats = builder.write_circuit(netlist, out)
    except builder.BuildError as exc:
        raise ToolError(str(exc)) from exc

    result: Dict[str, object] = {"path": str(out), "stats": stats}
    project = load_project(out)
    c = project.circuit()

    if smoke_test:
        rt = _runtime()
        sim_project, sim_c, orig_c, derived = _prepare(str(out), c.name)
        ins_all, outs, _ = truth.signal_pins(sim_c)
        user_labels = {p.label for p in truth.signal_pins(orig_c)[0]}
        assign = {p.label: 0 for p in ins_all if p.label in user_labels}
        if assign and outs:
            try:
                truth.evaluate_rows(rt, sim_project, sim_c, [assign], timeout=120, derived=derived)
                result["logisim_load"] = "ok"
            except (ToolError, RuntimeError, ValueError) as exc:
                result["logisim_load"] = "failed"
                result["logisim_error"] = str(exc)
        else:
            result["logisim_load"] = "skipped (no labelled pins)"

    check = lint.lint_circuit(project, c)
    result["lint"] = {
        "errors": check["summary"]["errors"],
        "warnings": check["summary"]["warnings"],
        "issues": check["issues"][:10],
    }
    return result


def circuit_lint(path: str, circuit: Optional[str] = None) -> Dict[str, object]:
    project = _project(path)
    c = _circuit(project, circuit)
    return lint.lint_circuit(project, c)


def circuit_retrofit(
    path: str,
    output: Optional[str] = None,
    write: bool = True,
    verify: bool = True,
) -> Dict[str, object]:
    """把 .circ 升级为 5.0 原生工程骨架（全量元件库 + 工具栏 + 鼠标绑定 + 选项）。"""
    p = _resolve(path)
    if not p.exists():
        raise ToolError(f"找不到电路文件: {p}")
    try:
        info = upgrade.retrofit(
            p,
            write=write,
            verify=verify,
            output=_resolve(output) if output else None,
        )
    except (ValueError, OSError) as exc:
        raise ToolError(str(exc)) from exc
    info["hint"] = (
        "工程头已补全：GUI 左侧元件库面板会显示全部内置库，顶部快捷工具条恢复默认按钮。"
        if write
        else "预演模式（write=false），未改动文件。"
    )
    return info
