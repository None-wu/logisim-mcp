"""Logisim-evolution .circ 文件模型与解析。

只依赖标准库。兼容 5.x（source="5.0.0"）以及旧版 3.x/4.x 的常见结构：
根元素 <project>，内含 <lib>、<main>、<circuit>、<options>、<mappings>、<toolbar>。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

Point = Tuple[int, int]

_LOC_RE = re.compile(r"\(\s*(-?\d+)\s*,\s*(-?\d+)\s*\)")


def parse_loc(text: str) -> Point:
    m = _LOC_RE.match(text.strip())
    if not m:
        raise ValueError(f"非法坐标: {text!r}")
    return int(m.group(1)), int(m.group(2))


def fmt_loc(p: Point) -> str:
    return f"({p[0]},{p[1]})"


@dataclass
class Component:
    name: str
    loc: Point
    lib: Optional[str] = None
    attrs: Dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.attrs.get("label", "").strip()

    @property
    def width(self) -> int:
        try:
            return max(1, int(self.attrs.get("width", "1")))
        except ValueError:
            return 1

    @property
    def facing(self) -> str:
        return self.attrs.get("facing", "east")

    def attr(self, key: str, default: str = "") -> str:
        return self.attrs.get(key, default)


@dataclass
class Wire:
    frm: Point
    to: Point

    def horizontal(self) -> bool:
        return self.frm[1] == self.to[1]

    def vertical(self) -> bool:
        return self.frm[0] == self.to[0]

    def contains_interior(self, p: Point) -> bool:
        """点是否严格位于线段内部（不含端点）。"""
        (x1, y1), (x2, y2) = self.frm, self.to
        if x1 == x2 == p[0]:
            return min(y1, y2) < p[1] < max(y1, y2)
        if y1 == y2 == p[1]:
            return min(x1, x2) < p[0] < max(x1, x2)
        return False


@dataclass
class Circuit:
    name: str
    comps: List[Component] = field(default_factory=list)
    wires: List[Wire] = field(default_factory=list)
    attrs: Dict[str, str] = field(default_factory=dict)

    def pins(self) -> List[Component]:
        return [c for c in self.comps if c.name == "Pin"]

    def input_pins(self) -> List[Component]:
        return [c for c in self.pins() if c.attr("type", "input") != "output"]

    def output_pins(self) -> List[Component]:
        return [c for c in self.pins() if c.attr("type", "input") == "output"]

    def comp(self, name: str) -> List[Component]:
        return [c for c in self.comps if c.name == name]

    def components_by_name(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for c in self.comps:
            out[c.name] = out.get(c.name, 0) + 1
        return out


@dataclass
class Project:
    path: Optional[Path]
    source: str
    version: str
    libs: List[str] = field(default_factory=list)
    lib_desc: Dict[str, str] = field(default_factory=dict)
    main: str = "main"
    circuits: Dict[str, Circuit] = field(default_factory=dict)
    options: Dict[str, str] = field(default_factory=dict)
    raw_root: Optional[ET.Element] = None

    def circuit(self, name: Optional[str] = None) -> Circuit:
        key = name or self.main
        if key not in self.circuits:
            raise KeyError(
                f"电路 {key!r} 不存在；可用电路: {', '.join(self.circuits) or '(空)'}"
            )
        return self.circuits[key]

    def is_subcircuit(self, comp: Component) -> bool:
        return comp.name in self.circuits and comp.name != "Pin"


def _read_attrs(el: ET.Element) -> Dict[str, str]:
    return {a.get("name", ""): a.get("val", "") for a in el.findall("a")}


def load_project(path: str | Path) -> Project:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到电路文件: {p}")
    try:
        root = ET.parse(p).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"{p.name} 不是合法的 XML: {exc}") from exc
    if root.tag != "project":
        raise ValueError(f"{p.name} 不是 Logisim 工程文件（根元素为 <{root.tag}>）")

    project = Project(
        path=p,
        source=root.get("source", "unknown"),
        version=root.get("version", "1.0"),
        raw_root=root,
    )

    for el in root:
        if el.tag == "lib":
            name = el.get("name", "")
            project.libs.append(name)
            project.lib_desc[name] = el.get("desc", "")
        elif el.tag == "main":
            project.main = el.get("name", "main")
        elif el.tag == "options":
            project.options.update(_read_attrs(el))
        elif el.tag == "circuit":
            circuit = Circuit(name=el.get("name", ""), attrs=_read_attrs(el))
            for sub in el:
                if sub.tag == "comp":
                    circuit.comps.append(
                        Component(
                            name=sub.get("name", ""),
                            loc=parse_loc(sub.get("loc", "(0,0)")),
                            lib=sub.get("lib"),
                            attrs=_read_attrs(sub),
                        )
                    )
                elif sub.tag == "wire":
                    circuit.wires.append(
                        Wire(parse_loc(sub.get("from", "(0,0)")), parse_loc(sub.get("to", "(0,0)")))
                    )
            project.circuits[circuit.name] = circuit

    if not project.circuits:
        raise ValueError(f"{p.name} 中没有任何电路")
    if project.main not in project.circuits:
        project.main = next(iter(project.circuits))
    return project


def find_circ_files(root: str | Path, limit: int = 200) -> List[Path]:
    p = Path(root)
    if p.is_file():
        return [p]
    return sorted(p.rglob("*.circ"))[:limit]
