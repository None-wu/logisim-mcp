"""连线网络（net）追踪。

Logisim 的连通规则：
  * 导线只在"端点重合"处相连，十字交叉不算连接（需要放 Junction 才连）。
  * 元件的端口落在导线上（含线段内部）时相连。
  * 同名 Tunnel 在同一个电路内视为相连。

这里用并查集把所有点合并成网络，供 lint / 结构分析使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .circ import Circuit, Component, Point, Wire
from .geometry import Port, ports_of


class _DSU:
    def __init__(self) -> None:
        self.parent: Dict[Point, Point] = {}

    def find(self, x: Point) -> Point:
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: Point, b: Point) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class Net:
    id: int
    points: List[Point] = field(default_factory=list)
    ports: List[Tuple[Component, Port]] = field(default_factory=list)
    label: str = ""
    tunnel: bool = False

    @property
    def drivers(self) -> List[Tuple[Component, Port]]:
        return [(c, p) for c, p in self.ports if p.direction == "output"]

    @property
    def sinks(self) -> List[Tuple[Component, Port]]:
        return [(c, p) for c, p in self.ports if p.direction == "input"]

    @property
    def width(self) -> int:
        for _, p in self.ports:
            if p.direction == "output":
                return p.width
        return self.ports[0][1].width if self.ports else 1

    def as_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "width": self.width,
            "drivers": [f"{c.name}@{c.loc}" for c, _ in self.drivers],
            "sinks": [f"{c.name}@{c.loc}" for c, _ in self.sinks],
        }


@dataclass
class Netlist:
    nets: List[Net]
    point_net: Dict[Point, int]
    unsupported: List[Component]
    dangling_endpoints: List[Point]

    def net_at(self, p: Point) -> Optional[Net]:
        idx = self.point_net.get(p)
        return self.nets[idx] if idx is not None else None


def build_netlist(circuit: Circuit) -> Netlist:
    dsu = _DSU()
    for w in circuit.wires:
        dsu.union(w.frm, w.to)

    # 按坐标分桶，避免每个点都遍历所有导线
    horiz: Dict[int, List[Wire]] = {}
    vert: Dict[int, List[Wire]] = {}
    for w in circuit.wires:
        if w.horizontal():
            horiz.setdefault(w.frm[1], []).append(w)
        elif w.vertical():
            vert.setdefault(w.frm[0], []).append(w)

    ports_by_comp: List[Tuple[Component, Port]] = []
    unsupported: List[Component] = []
    for comp in circuit.comps:
        ports = ports_of(comp)
        if ports is None:
            if comp.name not in ("Splitter", "Junction"):
                unsupported.append(comp)
            continue
        for p in ports:
            ports_by_comp.append((comp, p))

    points = set()
    for w in circuit.wires:
        points.add(w.frm)
        points.add(w.to)
    for _, p in ports_by_comp:
        points.add(p.loc)

    # 端点落在导线内部 => 视为 T 型连接
    for pt in points:
        x, y = pt
        for w in horiz.get(y, ()):  # 同一水平线上的导线
            if w.contains_interior(pt):
                dsu.union(pt, w.frm)
        for w in vert.get(x, ()):
            if w.contains_interior(pt):
                dsu.union(pt, w.frm)

    # 同名 Tunnel 相连
    tunnels: Dict[str, Point] = {}
    for comp in circuit.comps:
        if comp.name == "Tunnel" and comp.label:
            if comp.label in tunnels:
                dsu.union(tunnels[comp.label], comp.loc)
            else:
                tunnels[comp.label] = comp.loc

    # Splitter 的合并端与各分支端在 Logisim 内部导通（无导线相连），
    # 这里把同一 Splitter 的所有端口点并入同一网络。
    for comp in circuit.comps:
        if comp.name != "Splitter":
            continue
        ports = ports_of(comp)
        if not ports:
            continue
        first = ports[0].loc
        for p in ports[1:]:
            dsu.union(first, p.loc)

    groups: Dict[Point, List[Tuple[Component, Port]]] = {}
    for comp, p in ports_by_comp:
        groups.setdefault(dsu.find(p.loc), []).append((comp, p))

    point_net: Dict[Point, int] = {}
    nets: List[Net] = []
    for root, plist in groups.items():
        net = Net(id=len(nets), ports=plist)
        labels = [p.label for _, p in plist if p.label and p.direction == "output"]
        tunnel_label = [p.label for c, p in plist if c.name == "Tunnel" and p.label]
        net.label = (labels or tunnel_label or [""])[0]
        net.tunnel = bool(tunnel_label)
        for _, p in plist:
            if p.loc not in point_net:
                point_net[p.loc] = net.id
                net.points.append(p.loc)
        nets.append(net)

    # 找出所有"悬空"的导线端点：不与任何端口/其它导线相交
    dangling: List[Point] = []
    occupied = {p.loc for _, p in ports_by_comp}
    endpoint_count: Dict[Point, int] = {}
    for w in circuit.wires:
        endpoint_count[w.frm] = endpoint_count.get(w.frm, 0) + 1
        endpoint_count[w.to] = endpoint_count.get(w.to, 0) + 1
    for pt in points:
        if pt in occupied or endpoint_count.get(pt, 0) > 1:
            continue
        x, y = pt
        touches = any(w.contains_interior(pt) for w in horiz.get(y, ())) or any(
            w.contains_interior(pt) for w in vert.get(x, ())
        )
        if not touches:
            dangling.append(pt)

    return Netlist(nets=nets, point_net=point_net, unsupported=unsupported, dangling_endpoints=dangling)
