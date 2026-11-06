"""Splitter 无头仿真兼容层。

实测结论：Logisim-evolution v5.0.0 的无头仿真引擎（--test-vector 与 -tty table
两条路径）都不支持 Splitter——分支与合并端之间不导通（GUI 正常）。为了让
MCP 能自动验证含 Splitter 的电路，这里做等价变换：

  * 合并方向（单比特引脚 → 分支 → 总线）：
      删除 Splitter，在合并端 end0 位置放一个 fanout 位输入引脚 BUS；
      仿真时 BUS 的取值由各分支驱动的单比特引脚按位合成。
  * 分裂方向（总线 → 分支 → 单比特消费）：
      删除 Splitter，在每个分支端放一个 1 位输入引脚 BUSbK；
      仿真时各分支引脚的取值由总线值按位拆出。

变换后的电路仅用于无头仿真，用户文件保持原样（GUI 中 Splitter 完全正常）。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .circ import Circuit, Project, load_project
from .geometry import ports_of
from .nets import _DSU


class CompatError(ValueError):
    pass


@dataclass
class Bus:
    """一个 Splitter 的等价变换方案。"""

    label: str          # 合并方向下新增总线引脚的标签；分裂方向为原总线引脚标签
    width: int          # fanout
    mode: str           # "merge" | "split"
    loc: Tuple[int, int]
    # merge: {bit: 源引脚标签 或 ("const", 值)}
    # split: {bit: 新增分支引脚标签}
    bits: Dict[int, object] = field(default_factory=dict)


def needs_compat(circuit: Circuit) -> bool:
    return any(c.name == "Splitter" for c in circuit.comps)


def _as_int(v: object) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    s = str(v).strip().lower().replace("_", "")
    try:
        if s.startswith("0b"):
            return int(s[2:], 2)
        if s.startswith("0x"):
            return int(s[2:], 16)
        if s.startswith("0o"):
            return int(s[2:], 8)
        if s and all(ch in "01" for ch in s) and len(s) > 1:
            return int(s, 2)
        return int(s, 10)
    except ValueError as exc:
        raise CompatError(f"无法把取值 {v!r} 解析为整数") from exc


def _wire_groups(circuit: Circuit) -> _DSU:
    """只按导线（含 T 型内点接触）做连通；刻意不含 Splitter 的内部导通。"""
    dsu = _DSU()
    horiz: Dict[int, List] = {}
    vert: Dict[int, List] = {}
    for w in circuit.wires:
        dsu.union(w.frm, w.to)
        if w.horizontal():
            horiz.setdefault(w.frm[1], []).append(w)
        elif w.vertical():
            vert.setdefault(w.frm[0], []).append(w)

    def touches(pt) -> None:
        x, y = pt
        for w in horiz.get(y, ()):
            if w.contains_interior(pt):
                dsu.union(pt, w.frm)
        for w in vert.get(x, ()):
            if w.contains_interior(pt):
                dsu.union(pt, w.frm)

    for w in circuit.wires:
        touches(w.frm)
        touches(w.to)
    return dsu


def _const_value(comp) -> int:
    raw = comp.attrs.get("value", "0x0")
    try:
        return int(str(raw), 16) if str(raw).startswith("0x") else int(str(raw), 0)
    except ValueError:
        return 0


def _drivers_at(circuit: Circuit, dsu: _DSU, pt) -> List[Tuple[str, object]]:
    """找出与点 pt（经导线）连通的驱动源：输入引脚 / 常量。"""
    root = dsu.find(pt)
    out: List[Tuple[str, object]] = []
    for comp in circuit.comps:
        if comp.name == "Pin" and comp.attr("type", "input") == "input" and comp.label:
            if dsu.find(comp.loc) == root:
                out.append(("pin", comp.label))
        elif comp.name == "Constant":
            if dsu.find(comp.loc) == root:
                out.append(("const", _const_value(comp)))
    return out


def _take_label(circuit: Circuit, width: int) -> str:
    used = {c.label for c in circuit.comps if c.label}
    i = 1
    while f"BUS{i}" in used:
        i += 1
    return f"BUS{i}"


def analyze(circuit: Circuit) -> List[Bus]:
    """分析电路中每个 Splitter 的连接方向与位映射。"""
    buses: List[Bus] = []
    for comp in circuit.comps:
        if comp.name != "Splitter":
            continue
        try:
            fanout = int(comp.attrs.get("fanout", "2"))
        except ValueError:
            fanout = 2
        fanout = max(2, fanout)
        ports = ports_of(comp) or []
        if len(ports) != fanout + 1:
            raise CompatError(f"Splitter@{comp.loc} 端口几何解析失败")
        end0 = ports[0].loc
        branch = {p.index: p.loc for p in ports[1:]}  # index = end 序号(1 基)

        # bitN 属性 → end 序号；缺省 bit_{i-1} ↔ end_i
        bit_end: Dict[int, int] = {i - 1: i for i in range(1, fanout + 1)}
        for k in range(fanout):
            raw = comp.attrs.get(f"bit{k}")
            if raw is not None:
                try:
                    bit_end[k] = int(raw)
                except ValueError:
                    pass

        dsu = _wire_groups(circuit)
        merged_drv = _drivers_at(circuit, dsu, end0)

        if any(kind == "pin" for kind, _ in merged_drv):
            # 分裂方向：总线引脚驱动合并端
            label = next(lbl for kind, lbl in merged_drv if kind == "pin")
            bus = Bus(label=label, width=fanout, mode="split", loc=end0)
            for bit, end in bit_end.items():
                bus.bits[bit] = f"{label}b{bit}"  # 新增分支引脚标签
            buses.append(bus)
            continue

        # 合并方向：各分支由单比特引脚/常量驱动
        bits: Dict[int, object] = {}
        saw_driver = False
        for bit, end in bit_end.items():
            drv = _drivers_at(circuit, dsu, branch[end])
            if not drv:
                bits[bit] = ("const", 0)  # 悬空分支按 0 处理
                continue
            kind, val = drv[0]
            saw_driver = True
            if kind == "pin":
                bits[bit] = val
            else:
                bits[bit] = ("const", int(val))
        if not saw_driver:
            continue  # Splitter 两侧都没接驱动源，跳过
        buses.append(
            Bus(label=_take_label(circuit, fanout), width=fanout, mode="merge", loc=end0, bits=bits)
        )
    return buses


def derived_spec(buses: List[Bus]) -> Dict[str, Dict[str, object]]:
    """转成可 JSON 序列化的派生列描述：
    merge: {"BUS1": {"width": N, "mode": "merge", "sources": {bit: label 或 ["const", v]}}}
    split: {"BUS1b0": {"width": 1, "mode": "split", "bus": "BUS1", "bit": 0}}
    """
    spec: Dict[str, Dict[str, object]] = {}
    for bus in buses:
        if bus.mode == "merge":
            sources: Dict[str, object] = {}
            for bit, src in bus.bits.items():
                sources[str(bit)] = src if isinstance(src, str) else [src[0], src[1]]
            spec[bus.label] = {"width": bus.width, "mode": "merge", "sources": sources}
        else:
            for bit, lbl in bus.bits.items():
                spec[str(lbl)] = {"width": 1, "mode": "split", "bus": bus.label, "bit": bit}
    return spec


def derived_values(spec: Dict[str, Dict[str, object]], values: Dict[str, object]) -> Dict[str, int]:
    """根据用户输入取值计算全部派生列。values: 用户引脚标签 → 整数。"""
    out: Dict[str, int] = {}
    for col, d in spec.items():
        if d["mode"] == "merge":
            acc = 0
            for bit_str, src in d["sources"].items():
                bit = int(bit_str)
                if isinstance(src, list):
                    acc |= (int(src[1]) & 1) << bit
                else:
                    if src not in values:
                        raise CompatError(f"派生列 {col} 需要输入 {src} 的取值")
                    acc |= (_as_int(values[src]) & 1) << bit
            out[col] = acc
        else:
            bus_val = values.get(str(d["bus"]))
            if bus_val is None:
                raise CompatError(f"派生列 {col} 需要总线 {d['bus']} 的取值")
            out[col] = (_as_int(bus_val) >> int(d["bit"])) & 1
    return out


_PIN_XML = (
    '    <comp lib="0" loc="{loc}" name="Pin">\n'
    '      <a name="facing" val="{facing}"/>\n'
    '      <a name="type" val="input"/>\n'
    '      <a name="label" val="{label}"/>\n'
    "    </comp>"
)


def transform(project: Project, circuit: Circuit, out_dir: Path) -> Tuple[Path, List[Bus]]:
    """生成去掉 Splitter 的临时 .circ（仅目标电路），返回 (路径, 分析结果)。"""
    buses = analyze(circuit)
    if not buses:
        raise CompatError("电路中没有可分析的 Splitter 连接")

    tree = ET.parse(str(project.path))
    root = tree.getroot()
    target = None
    for cel in root.findall("circuit"):
        if cel.get("name") == circuit.name:
            target = cel
            break
    if target is None:
        raise CompatError(f"XML 中找不到电路 {circuit.name}")

    for comp in list(target.findall("comp")):
        if comp.get("name") == "Splitter":
            target.remove(comp)

    for bus in buses:
        if bus.mode == "merge":
            el = ET.fromstring(
                _PIN_XML.format(
                    loc=f"({bus.loc[0]},{bus.loc[1]})",
                    facing="west",
                    label=bus.label,
                )
            )
            w = ET.SubElement(el, "a")
            w.set("name", "width")
            w.set("val", str(bus.width))
            target.append(el)
        else:
            # split 模式：在每个分支端放 1 位引脚（位置 = end0 + gap*end）。
            # Bus.loc 即 end0；生成器只产出默认间距（gap=10）。
            for bit in bus.bits:
                end = bit + 1
                pt = (bus.loc[0], bus.loc[1] + 10 * end)
                el = ET.fromstring(
                    _PIN_XML.format(
                        loc=f"({pt[0]},{pt[1]})",
                        facing="east",
                        label=bus.bits[bit],
                    )
                )
                target.append(el)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(project.path).stem}__headless.circ"
    tree.write(str(out_path), encoding="unicode", xml_declaration=True)
    # 回读验证
    load_project(out_path)
    return out_path, buses
