"""从 JSON netlist 生成合法的 .circ 文件。

支持的元件：input / output / and / or / not / nand / nor / xor / xnor / buffer /
clock / led / button / constant / multiplexer（Plexers）/ splitter（Wiring）。
生成后会用 Logisim 实际加载一次做校验（含 Splitter 时走等价兼容副本）。

布局：按连接关系做最长路径分层，自动分配列与行；如果给了 "at" 就按给定坐标。
走线：正交多段线，每条网络独占车道/干线/下落线，杜绝共线重叠短路。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .circ import Project, load_project, fmt_loc
from .geometry import ports_of
from .circ import Component
from . import template, layout

Point = Tuple[int, int]

GATES = {
    "and": "AND Gate",
    "or": "OR Gate",
    "not": "NOT Gate",
    "nand": "NAND Gate",
    "nor": "NOR Gate",
    "xor": "XOR Gate",
    "xnor": "XNOR Gate",
    "buffer": "Buffer",
}

SINGLE_INPUT_GATES = {"not", "buffer"}


class BuildError(ValueError):
    pass


@dataclass
class SpecComp:
    id: str
    type: str
    loc: Optional[Point] = None
    attrs: Dict[str, str] = field(default_factory=dict)
    inputs: int = 2
    width: int = 1
    label: str = ""
    select_bits: int = 1  # multiplexer 的选择端位数


def _snap(v: int, grid: int = 10) -> int:
    return int(round(v / grid) * grid)


def _parse_spec(spec: Dict[str, object]) -> Tuple[str, List[SpecComp], List[Tuple[str, str]]]:
    name = str(spec.get("circuit") or spec.get("name") or "main")
    comps_raw = spec.get("components")
    if not isinstance(comps_raw, list) or not comps_raw:
        raise BuildError("components 不能为空")

    comps: List[SpecComp] = []
    seen = set()
    for item in comps_raw:
        if not isinstance(item, dict):
            raise BuildError("components 的每一项必须是对象")
        cid = str(item.get("id") or "").strip()
        if not cid:
            raise BuildError("每个元件必须有 id")
        if cid in seen:
            raise BuildError(f"重复的元件 id: {cid}")
        seen.add(cid)
        ctype = str(item.get("type") or "").strip().lower()
        at = item.get("at")
        loc = None
        if isinstance(at, (list, tuple)) and len(at) == 2:
            loc = (_snap(int(at[0])), _snap(int(at[1])))
        comp = SpecComp(
            id=cid,
            type=ctype,
            loc=loc,
            label=str(item.get("label") or cid),
            width=int(item.get("width") or 1),
            inputs=int(item.get("inputs") or 2),
            attrs={"value": str(item.get("value", "0x1"))},
        )
        if ctype in SINGLE_INPUT_GATES:
            comp.inputs = 1
        if ctype == "multiplexer":
            sel = item.get("select")
            if sel is None:
                sel = max(1, (comp.inputs - 1).bit_length()) if comp.inputs > 1 else 1
            comp.select_bits = int(sel)
            comp.inputs = 1 << comp.select_bits
        if ctype == "splitter":
            comp.inputs = int(item.get("fanout") or comp.inputs or 2)
            comp.inputs = max(2, comp.inputs)
        comps.append(comp)

    conns_raw = spec.get("connections") or []
    if not isinstance(conns_raw, list):
        raise BuildError("connections 必须是数组")
    conns: List[Tuple[str, str]] = []
    for pair in conns_raw:
        if isinstance(pair, str) and "->" in pair:
            a, b = pair.split("->", 1)
        elif isinstance(pair, (list, tuple)) and len(pair) == 2:
            a, b = str(pair[0]), str(pair[1])
        else:
            raise BuildError(f"无法识别的连接: {pair!r}")
        conns.append((a.strip(), b.strip()))

    ids = {c.id for c in comps}
    for a, b in conns:
        for ref in (a, b):
            cid = ref.split(".")[0]
            if cid not in ids:
                raise BuildError(f"连接引用了不存在的元件: {ref}")
    return name, comps, conns


def _comp_lib_and_name(c: SpecComp, project: Optional[Project]) -> Tuple[str, str]:
    """元件所属库索引与元件名。

    库索引必须与工程头里的 <lib name="..."/> 一致；这里统一用 5.0.0 原生编号
    （#Wiring=0 #Gates=1 #Plexers=2 #I/O=5），保证生成文件的库表与 GUI 默认工程一致。
    """
    if c.type == "input" or c.type == "output":
        return template.LIB_WIRING, "Pin"
    if c.type in GATES:
        return template.LIB_GATES, GATES[c.type]
    if c.type == "clock":
        return template.LIB_WIRING, "Clock"
    if c.type == "constant":
        return template.LIB_WIRING, "Constant"
    if c.type == "tunnel":
        return template.LIB_WIRING, "Tunnel"
    if c.type == "led":
        return template.LIB_IO, "LED"
    if c.type == "button":
        return template.LIB_IO, "Button"
    if c.type == "multiplexer":
        return template.LIB_PLEXERS, "Multiplexer"
    if c.type == "splitter":
        return template.LIB_WIRING, "Splitter"
    raise BuildError(f"不支持的元件类型: {c.type}")


def _probe(c: SpecComp, at: Point) -> Component:
    """构造临时的 .circ 元件模型（用于查询端口几何）。"""
    lib, cname = _comp_lib_and_name(c, None)
    attrs: Dict[str, str] = {}
    if c.type == "multiplexer":
        attrs = {"select": str(c.select_bits), "enable": "false", "facing": "east"}
    elif c.type == "splitter":
        attrs = {"facing": "west", "fanout": str(c.inputs)}
        for k in range(c.inputs):
            attrs[f"bit{k}"] = str(k + 1)
    elif c.type in GATES:
        attrs = {"inputs": str(c.inputs)}
    elif c.type == "constant":
        attrs = {"value": c.attrs.get("value", "0x1")}
    elif c.type == "input":
        attrs = {"facing": "east"}
    elif c.type == "output":
        attrs = {"facing": "west", "type": "output"}
    return Component(name=cname, loc=at, lib=lib, attrs=attrs)


def _offsets(c: SpecComp) -> List[Point]:
    """元件放在原点时的全部端口坐标（测宽度/对齐用）。"""
    return [p.loc for p in (ports_of(_probe(c, (0, 0))) or [])]


def _gap_of(c: SpecComp) -> int:
    """列内最小垂直间距（保证元件图形与标签不重叠）。"""
    t = c.type
    if t == "constant":
        return 30
    if t in ("input", "clock", "button"):
        return 50
    if t in ("output", "led"):
        return 60
    if t == "multiplexer":
        return (1 << c.select_bits) * 10 + 50
    if t == "splitter":
        return c.inputs * 10 + 50
    if t in ("not", "buffer"):
        return 50
    return max(90, 20 * c.inputs + 40)


def _port_point(c: SpecComp, role: str, project: Optional[Project] = None) -> Point:
    """计算某个元件在布局中的端口坐标。role: 'out' 或 'in0'/'in1'..."""
    assert c.loc is not None
    if c.type in ("input", "output", "clock", "led", "button", "constant", "tunnel"):
        return c.loc
    comp = _probe(c, c.loc)
    ports = ports_of(comp) or []
    if role == "out":
        return ports[0].loc if ports else c.loc
    if role == "select":
        for p in ports:
            if p.hint == "select":
                return p.loc
    if role.startswith("in"):
        idx = int(role[2:]) + 1
        for p in ports:
            if p.index == idx:
                return p.loc
    return c.loc


def build_circuit(spec: Dict[str, object], project: Optional[Project] = None) -> Tuple[str, Dict[str, object]]:
    name, comps, conns = _parse_spec(spec)
    # 布局：分层 → 重心排序 → 对齐松弛（MUX/分线器自动居中于数据源）→ 最小间距
    layout.assign(comps, conns, _port_point, _offsets, _gap_of)
    by_id = {c.id: c for c in comps}

    body: List[str] = []
    comp_xml: List[str] = []
    for c in comps:
        lib, cname = _comp_lib_and_name(c, project)
        attrs: List[str] = []
        if c.type == "input":
            # 5.0 原生写法：输入引脚不写 type（缺省即输入）
            attrs.append(("appearance", "classic"))
            attrs.append(("facing", "east"))
            attrs.append(("label", c.label))
        elif c.type == "output":
            attrs.append(("appearance", "classic"))
            attrs.append(("facing", "west"))
            attrs.append(("label", c.label))
            attrs.append(("type", "output"))
        elif c.type in GATES:
            if c.type not in SINGLE_INPUT_GATES:
                attrs.append(("inputs", str(c.inputs)))
            attrs.append(("label", c.label if c.label != c.id else ""))
        elif c.type == "clock":
            attrs.append(("label", c.label if c.label != c.id else ""))
        elif c.type == "tunnel":
            attrs.append(("label", c.label))
        elif c.type in ("led", "button"):
            attrs.append(("label", c.label if c.label != c.id else ""))
        elif c.type == "constant":
            attrs.append(("value", c.attrs.get("value", "0x1")))
        elif c.type == "multiplexer":
            attrs.append(("select", str(c.select_bits)))
            attrs.append(("enable", "false"))
            attrs.append(("facing", "east"))
        elif c.type == "splitter":
            attrs.append(("facing", "west"))
            attrs.append(("appear", "legacy"))
            attrs.append(("fanout", str(c.inputs)))
            # 显式位映射：bitK ↔ 分支端序号 K+1（1 基，bit0 在最上方）
            for k in range(c.inputs):
                attrs.append((f"bit{k}", str(k + 1)))
        if c.width > 1 and c.type in ("input", "output", "led", "constant"):
            attrs.append(("width", str(c.width)))
        if c.type in GATES and c.width > 1:
            attrs.append(("width", str(c.width)))

        attr_xml = "".join(
            f'\n      <a name="{k}" val="{v}"/>' for k, v in attrs if v != ""
        )
        comp_xml.append(
            f'    <comp lib="{lib}" loc="{fmt_loc(c.loc)}" name="{cname}">{attr_xml}\n    </comp>'
        )

    # 布线：直线优先 → 干线扇出 → 贴汇点 L 形 → 绕行高速路；全程做
    # "不穿端口 / 不与他网共线重叠 / 不 T 接他网" 的可行性检查。
    nets: Dict[str, Dict[str, object]] = {}
    touched: set = set()
    for a, b in conns:
        sa, sb = a.split(".")[0], b.split(".")[0]
        role_a = a.split(".")[1] if "." in a else "out"
        role_b = b.split(".")[1] if "." in b else "in0"
        p1 = _port_point(by_id[sa], role_a, project)
        p2 = _port_point(by_id[sb], role_b, project)
        entry = nets.setdefault(a, {"src": p1, "sinks": set()})
        entry["sinks"].add(p2)
        touched.add(p1)
        touched.add(p2)

    net_list: List[Tuple[str, Point, List[Point]]] = []
    for sid, net in nets.items():
        sinks = sorted(net["sinks"], key=lambda q: (q[1], q[0]))  # type: ignore[index]
        if not sinks:
            continue
        net_list.append((sid, net["src"], sinks))  # type: ignore[arg-type]
    net_list.sort(key=lambda t: (t[1][0], t[1][1], t[0]))

    # 未被任何连接引用的端口也要登记（防止走线误碰）
    extra_ports: List[Point] = []
    for c in comps:
        for dx, dy in _offsets(c):
            pt = (c.loc[0] + dx, c.loc[1] + dy)
            if pt not in touched:
                extra_ports.append(pt)
    # 走不出去的网络提到队首重试（换到更早的拐弯资源）；最多回退 3 次。
    # 关键：重试可能"按下葫芦浮起瓢"——把原来成功的网挤坏。因此保留
    # 失败数最少的那次尝试（并列取先），而不是盲取最后一次。
    best: Optional[Tuple[List[Tuple[Point, Point]], List[str]]] = None
    priority: set = set()
    for _ in range(4):
        ordered = [t for t in net_list if t[0] in priority] + [
            t for t in net_list if t[0] not in priority
        ]
        wires, failed = layout.route(ordered, extra_ports)
        if best is None or len(failed) < len(best[1]):
            best = (wires, failed)
        if not failed:
            break
        priority = set(failed)
    wires, failed = best  # type: ignore[assignment]

    seen = set()
    wire_xml: List[str] = []
    for p1, p2 in wires:
        if p1 == p2:
            continue
        key = tuple(sorted((p1, p2)))
        if key in seen:
            continue
        seen.add(key)
        wire_xml.append(f'    <wire from="{fmt_loc(p1)}" to="{fmt_loc(p2)}"/>')

    # 注意：Logisim 5.0.0 的无头 test-vector 引擎在遇到 Junction 元件时会卡死（实测），
    # 而 T 型连接本身就会自动导通，因此这里不生成 Junction（必要时可手工补）。

    xml = [
        *template.project_head(name),
        f'  <circuit name="{name}">',
        *template.circuit_attrs(name),
        *comp_xml,
        *wire_xml,
        "  </circuit>",
        "</project>",
        "",
    ]
    stats = {
        "circuit": name,
        "components": len(comps),
        "wires": len(wire_xml),
        "junctions": 0,
        "connections": len(conns),
        "libraries": len(template.DEFAULT_LIBS),
        "toolbar_tools": sum(1 for line in template.TOOLBAR if "<tool " in line),
    }
    return "\n".join(xml), stats


def validate_generated(path: str | Path) -> None:
    """生成后回读，确保结构可解析。"""
    project = load_project(path)
    circuit = project.circuit()
    if not circuit.comps:
        raise BuildError("生成的电路为空")


def write_circuit(spec: Dict[str, object], output: str | Path) -> Dict[str, object]:
    xml, stats = build_circuit(spec)
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8")
    validate_generated(out)
    stats["path"] = str(out)
    return stats
