"""元件端口几何模型。

Logisim 的连线只认"端口坐标完全重合"，因此要分析网络就必须知道每个元件
端口相对 loc 的偏移。这里覆盖最常用的组合/时序基础元件；遇到未知元件时
返回 None，调用方应把它当作不透明节点处理（不做连通性推断，避免误报）。

门的端口公式来自 logisim-evolution 的 AbstractGate#getInputOffset：
输出端口固定在 loc，输入端口在 loc 的基础上沿 facing 反方向偏移 axisLength，
垂直方向按输入序号对称分布。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .circ import Component, Point

GATE_NAMES = {
    "AND Gate": "and",
    "OR Gate": "or",
    "NOT Gate": "not",
    "NAND Gate": "nand",
    "NOR Gate": "nor",
    "XOR Gate": "xor",
    "XNOR Gate": "xnor",
    "Buffer": "buffer",
    "Odd Parity": "odd",
    "Even Parity": "even",
}

# 各门类型的额外轴长修正（实测 v5.0.0，单位为 Logisim 坐标）：
#   XOR/XNOR 的反向圆弧 +10；NAND/NOR/XNOR 输出端反相圆点 +10
_GATE_BONUS = {"XOR Gate": 10, "XNOR Gate": 10}
_GATE_NEGATE_OUT = {"NAND Gate": 10, "NOR Gate": 10, "XNOR Gate": 10}
# 默认尺寸：NOT=30、Buffer=20，其余 50
_DEFAULT_SIZE = {"NOT Gate": 30, "Buffer": 20}

SEQUENTIAL_NAMES = {
    "D Flip-Flop",
    "D Flip-Flop with Enable",
    "T Flip-Flop",
    "J-K Flip-Flop",
    "S-R Flip-Flop",
    "Register",
    "Counter",
    "Shift Register",
    "RAM",
    "ROM",
    "Random",
}


@dataclass
class Port:
    index: int
    loc: Point
    direction: str  # "input" | "output" | "passive"
    width: int = 1
    label: str = ""
    hint: str = ""  # out / in0 / in1 ...

    def as_dict(self) -> Dict[str, object]:
        return {
            "index": self.index,
            "loc": list(self.loc),
            "direction": self.direction,
            "width": self.width,
            "label": self.label,
        }


def _gate_geometry(comp: Component) -> Optional[List[Port]]:
    kind = GATE_NAMES.get(comp.name)
    if kind is None:
        return None
    single = kind in ("not", "buffer")
    try:
        inputs = 1 if single else int(comp.attrs.get("inputs", "2"))
    except ValueError:
        inputs = 2
    inputs = max(1, inputs)
    try:
        size = int(comp.attrs.get("size", str(_DEFAULT_SIZE.get(comp.name, 50))))
    except ValueError:
        size = _DEFAULT_SIZE.get(comp.name, 50)
    axis = size + _GATE_BONUS.get(comp.name, 0) + _GATE_NEGATE_OUT.get(comp.name, 0)
    try:
        negated = int(comp.attrs.get("negate", "0") or "0")
    except ValueError:
        negated = 0
    facing = comp.facing

    if inputs <= 3:
        if size < 40:
            skip_start, skip_dist, skip_lower_even = -5, 10, 10
        elif size < 60 or inputs <= 2:
            skip_start, skip_dist, skip_lower_even = -10, 20, 20
        else:
            skip_start, skip_dist, skip_lower_even = -15, 30, 30
    elif inputs == 4 and size >= 60:
        skip_start, skip_dist, skip_lower_even = -5, 20, 0
    else:
        skip_start, skip_dist, skip_lower_even = -5, 10, 10

    ports = [Port(0, comp.loc, "output", comp.width, comp.label, "out")]
    for i in range(inputs):
        if inputs % 2 == 1:
            dy = skip_start * (inputs - 1) + skip_dist * i
        else:
            dy = skip_start * inputs + skip_dist * i
            if i >= inputs // 2:
                dy += skip_lower_even
            if inputs == 4 and size >= 60:
                dy -= 10
        dx = axis
        if (negated >> i) & 1:
            dx += 10
        if facing == "north":
            off = (dy, dx)
        elif facing == "south":
            off = (dy, -dx)
        elif facing == "west":
            off = (dx, dy)
        else:
            off = (-dx, dy)
        loc = (comp.loc[0] + off[0], comp.loc[1] + off[1])
        ports.append(Port(i + 1, loc, "input", comp.width, comp.label, f"in{i}"))
    return ports


def _mux_geometry(comp: Component) -> Optional[List[Port]]:
    """Multiplexer（Plexers 库）。几何来自 std/plexers/Multiplexer#updatePorts：
    面东（默认）：数据输入在 loc 左侧 w 处，从 -(inputs/2)*10 每 10px 一个；
    选择端在 (-s, dy+10*inputs)；使能端在选择端沿 facing 再 +10；输出在 loc。
    仅支持 facing=east（生成器只产出面东的 MUX）。"""
    if comp.facing not in (None, "", "east"):
        return None
    try:
        sel_bits = int(comp.attrs.get("select", "1"))
    except ValueError:
        sel_bits = 1
    sel_bits = max(1, min(5, sel_bits))
    inputs = 1 << sel_bits
    wide = comp.attrs.get("size", "wide") != "narrow"
    w = 40 if wide else 20
    s = 20 if wide else 10
    ports = [Port(0, comp.loc, "output", comp.width, comp.label, "out")]
    dy0 = -(inputs // 2) * 10
    for i in range(inputs):
        ports.append(
            Port(
                i + 1,
                (comp.loc[0] - w, comp.loc[1] + dy0 + 10 * i),
                "input",
                comp.width,
                f"D{i}",
                f"in{i}",
            )
        )
    sel_x = -s if wide else -(s + 10)
    ports.append(
        Port(
            inputs + 1,
            (comp.loc[0] + sel_x, comp.loc[1] + dy0 + 10 * inputs),
            "input",
            sel_bits,
            "sel",
            "select",
        )
    )
    if comp.attrs.get("enable", "true") != "false":
        ports.append(
            Port(
                inputs + 2,
                (comp.loc[0] + sel_x + 10, comp.loc[1] + dy0 + 10 * inputs),
                "input",
                1,
                "en",
                "enable",
            )
        )
    return ports


def _splitter_geometry(comp: Component) -> Optional[List[Port]]:
    """Splitter（Wiring 库）。几何严格对齐 circuit/SplitterParameters（v5.0.0 源码）：
    水平 facing 时所有端口都在竖线 x = loc.x + m*20 上（m: east=+1 / west=-1）：
      合并端 end0 = (loc.x + m*20, loc.y - gap*⌊fanout/2⌋)；
      分支端 end_i (i=1..fanout) = (end0.x, end0.y + i*gap)；
    默认位映射 bit_{i-1} ↔ end_i（bit0 在最上方），与规范化文件中 bit0=1 一致。"""
    facing = comp.facing
    if facing not in ("east", "west"):
        return None
    try:
        fanout = int(comp.attrs.get("fanout", "2"))
    except ValueError:
        fanout = 2
    try:
        gap = int(comp.attrs.get("spacing", "1")) * 10
    except ValueError:
        gap = 10
    fanout = max(2, fanout)
    m = 1 if facing == "east" else -1
    end0 = (comp.loc[0] + m * 20, comp.loc[1] - gap * (fanout // 2))
    ports = [Port(0, end0, "passive", fanout, "", "out")]
    # 显式 bitN 属性（bitK val=分支端序号，1 基）；缺省 bit_{i-1} ↔ end_i
    bit_to_end: Dict[int, int] = {i - 1: i for i in range(1, fanout + 1)}
    for k in range(fanout):
        raw = comp.attrs.get(f"bit{k}")
        if raw is not None:
            try:
                bit_to_end[k] = int(raw)
            except ValueError:
                pass
    for bit, end in sorted(bit_to_end.items()):
        if 1 <= end <= fanout:
            ports.append(
                Port(
                    end,
                    (end0[0], end0[1] + gap * end),
                    "passive",
                    1,
                    f"bit{bit}",
                    f"in{bit}",
                )
            )
    return ports


def ports_of(comp: Component, project=None) -> Optional[List[Port]]:
    """返回元件端口列表；未知元件返回 None。"""
    name = comp.name

    if name == "Pin":
        # type="input" 表示这是"电路的输入引脚"，它向内部网络驱动信号；
        # type="output" 表示"电路的输出引脚"，它在内部是接收端。
        is_circuit_output = comp.attr("type", "input") == "output"
        return [Port(0, comp.loc, "input" if is_circuit_output else "output", comp.width, comp.label, "pin")]

    if name == "Tunnel":
        return [Port(0, comp.loc, "passive", comp.width, comp.label, "tunnel")]

    if name == "Clock":
        return [Port(0, comp.loc, "output", 1, comp.label, "clk")]

    if name == "Button":
        return [Port(0, comp.loc, "output", 1, comp.label, "btn")]

    if name == "Constant":
        return [Port(0, comp.loc, "output", comp.width, comp.label, "value")]

    if name in ("LED", "RGB LED", "Probe", "Lamp"):
        return [Port(0, comp.loc, "input", comp.width, comp.label, "in")]

    if name == "Junction":
        return [Port(0, comp.loc, "passive", 1, "", "junction")]

    gate = _gate_geometry(comp)
    if gate is not None:
        return gate

    if name == "Multiplexer":
        return _mux_geometry(comp)
    if name == "Splitter":
        return _splitter_geometry(comp)

    return None


def is_sequential(comp: Component) -> bool:
    return comp.name in SEQUENTIAL_NAMES
