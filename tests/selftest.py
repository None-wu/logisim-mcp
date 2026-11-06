#!/usr/bin/env python3
"""端到端自测：生成 -> 仿真 -> 真值表 -> 用例 -> lint -> MCP 协议。

运行：
    python tests/selftest.py
不需要任何第三方依赖；需要本机已安装 Logisim-evolution（本仓库同级的发行版即可）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from logisim_mcp import tools_impl as t  # noqa: E402
from logisim_mcp.mcp import McpServer, TOOLS  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  ok   {name}")
    else:
        FAIL.append(f"{name} {detail}")
        print(f"  FAIL {name} {detail}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="logisim-mcp-selftest-"))
    print(f"临时目录: {tmp}\n")

    print("[1] 环境探测")
    info = t.logisim_info()
    check("Logisim 可执行文件已找到", bool(info.get("found")), info.get("error", ""))
    if not info.get("found"):
        print("\n环境不可用，剩余用例跳过。")
        return 1

    print("[2] 生成半加器")
    spec = {
        "circuit": "half_adder",
        "components": [
            {"id": "a", "type": "input", "label": "a"},
            {"id": "b", "type": "input", "label": "b"},
            {"id": "g1", "type": "xor", "inputs": 2},
            {"id": "g2", "type": "and", "inputs": 2},
            {"id": "s", "type": "output", "label": "sum"},
            {"id": "c", "type": "output", "label": "carry"},
        ],
        "connections": [
            ["a", "g1.in0"], ["b", "g1.in1"],
            ["a", "g2.in0"], ["b", "g2.in1"],
            ["g1.out", "s"], ["g2.out", "c"],
        ],
    }
    created = t.circuit_create(str(tmp / "half_adder.circ"), spec)
    circ = created["path"]
    check("生成文件存在", Path(circ).exists())
    check("Logisim 能加载", created.get("logisim_load") == "ok", str(created.get("logisim_error", "")))

    print("[2b] 工程骨架完整性（GUI 元件库 / 工具栏）")
    from logisim_mcp import runner, template, upgrade  # noqa: E402

    text = Path(circ).read_text(encoding="utf-8")
    libs = text.count("<lib desc=")
    check("内置库全部声明", libs == len(template.DEFAULT_LIBS), f"{libs} 个")
    toolbar_body = text.split("<toolbar>")[1].split("</toolbar>")[0]
    check("工具栏非空", toolbar_body.count("<tool name=") >= 10, f"{toolbar_body.count('<tool name=')} 个工具")
    project = t.circuit_list(circ)
    check("工程可解析", bool(project.get("circuits")))

    # GUI 打开再保存（官方 -n）后，库表与工具栏必须原样保留
    rt = runner.find_runtime()
    norm = tmp / "half_adder_norm.circ"
    ok, err = runner.normalize(rt, circ, norm)
    ntext = norm.read_text(encoding="utf-8") if ok else ""
    check("官方 -n 重写成功", ok, err)
    check("重写后库表不缩水", ntext.count("<lib desc=") == len(template.DEFAULT_LIBS))
    check("重写后工具栏不丢", ntext.count("<tool name=") >= 10, f"{ntext.count('<tool name=')} 个工具")

    # 老文件（只声明少量库、空工具栏）可被 retrofit 补齐，且语义不变
    legacy = tmp / "legacy.circ"
    legacy.write_text(
        text.replace("\n".join(template.MAPPINGS), "  <mappings/>")
        .replace("\n".join(template.TOOLBAR), "  <toolbar/>"),
        encoding="utf-8",
    )
    rep = t.circuit_retrofit(str(legacy), verify=False)
    fixed = Path(rep["path"]).read_text(encoding="utf-8")
    check("retrofit 补全库表", fixed.count("<lib desc=") == len(template.DEFAULT_LIBS))
    check("retrofit 补全工具栏", "Poke Tool" in fixed and "D Flip-Flop" in fixed)
    check("retrofit 幂等", upgrade.retrofit(str(legacy), write=False)["changed"] is False)
    check("retrofit 后仿真不变", t.circuit_truth_table(str(legacy))["row_count"] == 4)

    print("[3] 真值表")
    tt = t.circuit_truth_table(circ)
    table = {(r["in"]["a"], r["in"]["b"]): (r["out"]["sum"], r["out"]["carry"]) for r in tt["rows"]}
    check("4 行", tt["row_count"] == 4)
    check("0+0", table[(0, 0)] == (0, 0))
    check("0+1", table[(0, 1)] == (1, 0))
    check("1+0", table[(1, 0)] == (1, 0))
    check("1+1", table[(1, 1)] == (0, 1))
    check("sum 表达式", tt["expressions"]["sum"] == "a&!b | !a&b", tt["expressions"]["sum"])
    check("carry 表达式", tt["expressions"]["carry"] == "a&b")

    print("[3b] 全加器（示例 netlist）")
    fa_spec = json.loads((ROOT / "examples" / "full_adder.netlist.json").read_text(encoding="utf-8"))
    fa = t.circuit_create(str(tmp / "full_adder.circ"), fa_spec)
    ftt = t.circuit_truth_table(fa["path"])
    fexp = {
        (0, 0, 0): (0, 0), (0, 0, 1): (1, 0), (0, 1, 0): (1, 0), (0, 1, 1): (0, 1),
        (1, 0, 0): (1, 0), (1, 0, 1): (0, 1), (1, 1, 0): (0, 1), (1, 1, 1): (1, 1),
    }
    fok = all(
        (r["out"]["sum"], r["out"]["cout"]) == fexp[(r["in"]["a"], r["in"]["b"], r["in"]["cin"])]
        for r in ftt["rows"]
    )
    check("全加器 8 行全对", fok and ftt["row_count"] == 8)
    check("cout 表达式", ftt["expressions"]["cout"] == "a&b | a&cin | b&cin", ftt["expressions"]["cout"])

    print("[3c] Splitter 兼容层（A/B/C → 分线器 → MUX 表决电路）")
    vs = {
        "circuit": "voter3s",
        "components": [
            {"id": "a", "type": "input", "label": "A"},
            {"id": "b", "type": "input", "label": "B"},
            {"id": "c", "type": "input", "label": "C"},
            {"id": "spl", "type": "splitter", "fanout": 3},
            {"id": "mux", "type": "multiplexer", "select": 3},
            {"id": "k0", "type": "constant", "value": "0x0"},
            {"id": "k1", "type": "constant", "value": "0x0"},
            {"id": "k2", "type": "constant", "value": "0x0"},
            {"id": "k3", "type": "constant", "value": "0x1"},
            {"id": "k4", "type": "constant", "value": "0x0"},
            {"id": "k5", "type": "constant", "value": "0x1"},
            {"id": "k6", "type": "constant", "value": "0x1"},
            {"id": "k7", "type": "constant", "value": "0x1"},
            {"id": "f", "type": "output", "label": "F"},
        ],
        "connections": [
            ["c", "spl.in0"], ["b", "spl.in1"], ["a", "spl.in2"],
            ["spl.out", "mux.select"],
            ["k0", "mux.in0"], ["k1", "mux.in1"], ["k2", "mux.in2"], ["k3", "mux.in3"],
            ["k4", "mux.in4"], ["k5", "mux.in5"], ["k6", "mux.in6"], ["k7", "mux.in7"],
            ["mux.out", "f"],
        ],
    }
    v3 = t.circuit_create(str(tmp / "voter3s.circ"), vs)
    vtt = t.circuit_truth_table(v3["path"])
    vmins = sorted(
        r["in"]["A"] * 4 + r["in"]["B"] * 2 + r["in"]["C"] for r in vtt["rows"] if r["out"]["F"] == 1
    )
    check("Splitter 表决 F=1 ∈ {3,5,6,7}", vmins == [3, 5, 6, 7], str(vmins))
    check("Splitter 兼容层 note", "Splitter" in (vtt.get("note") or ""))

    print("[4] 组合仿真 / 用例")
    sim = t.circuit_simulate(circ, inputs={"a": 1, "b": 1})
    check("1+1 输出", sim["outputs"] == {"carry": 1, "sum": 0}, str(sim["outputs"]))

    good = "a b sum carry\n0 0 0 0\n0 1 1 0\n1 0 1 0\n1 1 0 1\n"
    res = t.circuit_test(circ, vectors=good)
    check("正确用例全部通过", res["all_passed"], str(res["failed"]))

    bad = good.replace("1 1 0 1", "1 1 1 1")
    res_bad = t.circuit_test(circ, vectors=bad)
    check("错误用例被捕获", res_bad["failed"] == 1)
    check("报出期望/实际", res_bad["rows"][-1]["actual"]["carry"] == "1")

    print("[5] 多位宽总线")
    spec4 = {
        "circuit": "and4",
        "components": [
            {"id": "a", "type": "input", "label": "a", "width": 4},
            {"id": "b", "type": "input", "label": "b", "width": 4},
            {"id": "g", "type": "and", "inputs": 2, "width": 4},
            {"id": "y", "type": "output", "label": "y", "width": 4},
        ],
        "connections": [["a", "g.in0"], ["b", "g.in1"], ["g.out", "y"]],
    }
    c4 = t.circuit_create(str(tmp / "and4.circ"), spec4)
    check("4 位电路加载", c4.get("logisim_load") == "ok")
    r4 = t.circuit_simulate(c4["path"], inputs={"a": "0011", "b": "0101"})
    check("0011 & 0101 = 1", r4["outputs"]["y"] == 1, str(r4["outputs"]))
    tt4 = t.circuit_truth_table(c4["path"], max_rows=300)
    check("多比特不推导表达式", "expressions_skipped" in tt4)

    print("[6] 结构检查")
    lint = t.circuit_lint(circ)
    check("干净电路无 error", lint["summary"]["errors"] == 0)
    check("干净电路无 warning", lint["summary"]["warnings"] == 0, json.dumps(lint["issues"], ensure_ascii=False)[:300])

    broken = tmp / "broken.circ"
    broken.write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<project source="5.0.0" version="1.0">\n'
        '  <lib desc="#Wiring" name="0"/>\n'
        '  <lib desc="#Gates" name="1"/>\n'
        '  <lib desc="#I/O" name="2"/>\n'
        '  <main name="main"/>\n'
        '  <circuit name="main">\n'
        '    <comp lib="0" loc="(100,100)" name="Pin">\n'
        '      <a name="type" val="input"/><a name="label" val="a"/>\n'
        '    </comp>\n'
        '    <comp lib="0" loc="(200,100)" name="Pin">\n'
        '      <a name="type" val="input"/><a name="label" val="a"/>\n'
        '    </comp>\n'
        '    <wire from="(100,100)" to="(140,100)"/>\n'
        '    <wire from="(140,100)" to="(140,160)"/>\n'
        '  </circuit>\n'
        '</project>\n',
        encoding="utf-8",
    )
    lb = t.circuit_lint(str(broken))
    codes = {i["code"] for i in lb["issues"]}
    check("检出重复标签", "duplicate-label" in codes)
    check("检出悬空导线", "dangling-wire" in codes)

    print("[7] 结构读取")
    lst = t.circuit_list(circ)
    check("circuit_list 正常", lst["main"] == "half_adder" and lst["circuit_count"] == 1)
    ins = t.circuit_inspect(circ, include_nets=True)
    check("circuit_inspect 端口", ins["stats"]["pins"] == 4 and "nets" in ins)

    print("[8] MCP 协议")
    server = McpServer()
    init = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "selftest"}},
        }
    )
    check("握手返回 serverInfo", init["result"]["serverInfo"]["name"] == "logisim-evolution-mcp")
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    check("工具数量", len(listed["result"]["tools"]) == len(TOOLS))
    called = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "circuit_simulate", "arguments": {"path": circ, "inputs": {"a": 0, "b": 1}}},
        }
    )
    check("tools/call 可执行", not called["result"]["isError"], called["result"]["content"][0]["text"][:200])

    proc = subprocess.run(
        [sys.executable, str(ROOT / "server.py"), "--version"], capture_output=True, timeout=60
    )
    check("CLI --version", proc.returncode == 0 and b"logisim-evolution-mcp" in proc.stdout)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  - " + f)
    print(f"产物目录: {tmp}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
