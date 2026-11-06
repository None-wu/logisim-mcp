"""自动布局与美观布线。

设计目标（按优先级）：
1. 电气正确：任何一条线段都不得穿过别的网络的端口、不得与别的网络的线段
   共线重叠或端点相触（Logisim 里这些都会变成短路/误连）；同一网络的 T 型
   相接则是合法且有意的。
2. 美观：
   - 布局按最长路径分层；列内做重心排序（减少交叉）+ "对齐松弛"（让驱动源
     与负载的端口 y 互相靠拢，MUX/分线器自动垂直居中于自己的数据源），再按
     每类元件的最小间距推开。效果是常量簇拥在 MUX 旁、开关对齐分线器，而
     不是一字排开拉大扇。
   - 布线直线优先：y 对齐的直接拉直线；单汇点用贴着汇点的 L 形；多汇点用
     "干线"（一条竖线 + 短水平支线）；都不行再走上/下绕行高速路。
3. 可复现：同样的输入永远得到同样的图。

所有坐标都落在 10 的网格上（Logisim 画布网格）。
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

Point = Tuple[int, int]

CORRIDOR = 110  # 列间走线走廊宽度
TOP = 120       # 画布上边距
ROW = 80        # 列内初始行距
GRID = 10


def _snap(v: float) -> int:
    import math

    return int(math.floor(v / GRID + 0.5)) * GRID


def _mean(xs: Sequence[float], default: float) -> float:
    return sum(xs) / len(xs) if xs else default


# ---------------------------------------------------------------------------
# 布局
# ---------------------------------------------------------------------------


def _layers(comps, conns: Sequence[Tuple[str, str]]) -> Dict[str, int]:
    ids = [c.id for c in comps]
    succ: Dict[str, List[str]] = {i: [] for i in ids}
    indeg: Dict[str, int] = {i: 0 for i in ids}
    for a, b in conns:
        sa, sb = a.split(".")[0], b.split(".")[0]
        if sa == sb or sb not in succ or sa not in succ:
            continue
        if sb not in succ[sa]:
            succ[sa].append(sb)
            indeg[sb] += 1
    depth = {i: 0 for i in ids}
    queue = [i for i in ids if indeg[i] == 0]
    visited: Set[str] = set()
    while queue:
        i = queue.pop(0)
        visited.add(i)
        for j in succ[i]:
            depth[j] = max(depth[j], depth[i] + 1)
            indeg[j] -= 1
            if indeg[j] == 0:
                queue.append(j)
    for i in ids:  # 有环的节点退回第 0 列
        if i not in visited:
            depth[i] = 0
    return depth


def _rank(comp_type: str) -> int:
    """列内分组顺序：开关/时钟在最上，常量其次，其余按对齐目标排。"""
    if comp_type in ("input", "clock", "button"):
        return 0
    if comp_type == "constant":
        return 1
    return 2


def assign(
    comps,
    conns: Sequence[Tuple[str, str]],
    port_point: Callable,
    offsets: Callable,
    gap_of: Callable,
) -> None:
    """就地设置每个元件的 loc。带显式 at 的元件不动，其余自动布局。

    offsets(c)          -> 元件放在原点时的全部端口坐标（用于测宽）
    port_point(c, role) -> 当前位置下某端口坐标（要求 c.loc 已设置）
    gap_of(c)           -> 该元件在列内需要的最小垂直间距
    """
    if not comps:
        return
    fixed = {c.id for c in comps if c.loc is not None}
    by_id = {c.id: c for c in comps}
    depth = _layers(comps, conns)

    cols: Dict[int, List] = {}
    for c in comps:
        cols.setdefault(depth[c.id], []).append(c)

    # 列 x：以"最左端口"对齐；列宽 = 端口横向跨度 + 20 边距
    min_off: Dict[str, int] = {}
    width_of_col: Dict[int, int] = {}
    for d, col in cols.items():
        w = 0
        for c in col:
            mos = [p[0] for p in offsets(c)]
            min_off[c.id] = min(mos)
            w = max(w, max(mos) - min(mos) + 20)
        width_of_col[d] = w

    x = TOP
    col_x: Dict[int, int] = {}
    for d in sorted(cols):
        col_x[d] = x
        x += width_of_col[d] + CORRIDOR

    # 初始位置
    for d, col in cols.items():
        for i, c in enumerate(col):
            if c.id not in fixed:
                c.loc = (col_x[d] - min_off[c.id], TOP + i * ROW)
    if len(fixed) == len(comps):
        return  # 全部手工坐标，无需松弛

    # 连接（拆出端口角色）
    edges: List[Tuple[str, str, str, str]] = []
    for a, b in conns:
        sa, sb = a.split(".")[0], b.split(".")[0]
        role_a = a.split(".")[1] if "." in a else "out"
        role_b = b.split(".")[1] if "." in b else "in0"
        if sa in by_id and sb in by_id:
            edges.append((sa, role_a, sb, role_b))

    # 重心排序（减少交叉）
    order: Dict[int, List[str]] = {d: [c.id for c in cols[d]] for d in cols}
    for _ in range(2):
        depths = sorted(cols)
        for d in depths[1:]:
            key = {}
            for cid in order[d]:
                ys = [
                    port_point(by_id[o], ra)[1]
                    for (o, ra, t, rb) in edges
                    if t == cid
                ]
                key[cid] = _mean(ys, 1e18)
            order[d] = sorted(order[d], key=lambda cid: (key[cid], cid))
        for d in depths[:-1][::-1]:
            key = {}
            for cid in order[d]:
                ys = [
                    port_point(by_id[t], rb)[1]
                    for (o, ra, t, rb) in edges
                    if o == cid
                ]
                key[cid] = _mean(ys, -1e18)
            order[d] = sorted(order[d], key=lambda cid: (key[cid], cid))
    for d, col in cols.items():
        cols[d] = [by_id[cid] for cid in order[d]]

    # 对齐松弛 + 最小间距：按列序 Gauss–Seidel 迭代（本列的"对齐目标"用
    # 邻列的当前/刚更新位置计算，避免常量与 MUX 互相追逐的振荡）。
    for _ in range(6):
        for d in sorted(cols):
            col = cols[d]
            desired: Dict[str, List[float]] = {c.id: [] for c in col}
            for (o, ra, t, rb) in edges:
                if o not in by_id or t not in by_id:
                    continue
                if by_id[o].loc is None or by_id[t].loc is None:
                    continue
                if depth.get(o) != d and depth.get(t) != d:
                    continue  # 只更新与本列相关的边
                po = port_point(by_id[o], ra)
                pt = port_point(by_id[t], rb)
                # 让两端端口 y 重合所需的对方 loc.y
                if depth.get(t) == d:
                    desired[t].append(po[1] - (pt[1] - by_id[t].loc[1]))
                if depth.get(o) == d:
                    desired[o].append(pt[1] - (po[1] - by_id[o].loc[1]))

            movable = [c for c in col if c.id not in fixed]
            if not movable:
                continue
            movable.sort(
                key=lambda c: (_rank(c.type), _mean(desired[c.id], c.loc[1]), c.id)
            )
            placed: List[Tuple[str, int]] = []
            last: Optional[int] = None
            for c in movable:
                want = _snap(_mean(desired[c.id], c.loc[1]))
                y = want if last is None else max(want, last + gap_of(c))
                placed.append((c.id, y))
                last = y
            # 列整体居中到 desired 均值（把最小间距撑出的膨胀对称分摊），夹住上边界
            want_mean = _mean(
                [_mean(desired[cid], by_id[cid].loc[1]) for cid, _ in placed], 0.0
            )
            got_mean = sum(y for _, y in placed) / len(placed)
            shift = _snap(want_mean - got_mean)
            ys = [y + shift for _, y in placed]
            if min(ys) < TOP:
                ys = [y + (TOP - min(ys)) for y in ys]
            for (cid, _), y in zip(placed, ys):
                by_id[cid].loc = (by_id[cid].loc[0], y)


# ---------------------------------------------------------------------------
# 布线
# ---------------------------------------------------------------------------


def _on(pt: Point, p1: Point, p2: Point) -> bool:
    """点是否在线段上（含端点）。线段必为水平或垂直。"""
    (x1, y1), (x2, y2) = p1, p2
    if y1 == y2:
        return y1 == pt[1] and min(x1, x2) <= pt[0] <= max(x1, x2)
    if x1 == x2:
        return x1 == pt[0] and min(y1, y2) <= pt[1] <= max(y1, y2)
    return False


class Router:
    """记录已布线段与全部端口，提供"这条线段能不能加"的判定。"""

    def __init__(self) -> None:
        self.segs: List[Tuple[object, Point, Point]] = []
        self.ports: Dict[Point, object] = {}  # 端口点 → 所属网络
        self._multi = object()  # 哨兵：被多个网络引用的端口（多驱动，坏的电路）

    def add_port(self, net: object, pts: Iterable[Point]) -> None:
        for p in pts:
            old = self.ports.get(p)
            if old is None:
                self.ports[p] = net
            elif old is not net:
                self.ports[p] = self._multi

    def _hits_foreign_port(self, p1: Point, p2: Point, net: object) -> bool:
        for pt, owner in self.ports.items():
            if owner is net:
                continue
            if _on(pt, p1, p2):
                return True
        return False

    @staticmethod
    def _conflict(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
        """两条属于不同网络的线段能否共存？返回 True = 冲突。

        允许：垂直 × 水平且交点在双方内部（十字交叉，Logisim 不导通）。
        禁止：共线重叠、共线相触、端点落在对方线段上（T 接会导通）。
        """
        ph, qh = p1[1] == p2[1], q1[1] == q2[1]
        if ph and qh:
            if p1[1] != q1[1]:
                return False
            return max(min(p1[0], p2[0]), min(q1[0], q2[0])) <= min(
                max(p1[0], p2[0]), max(q1[0], q2[0])
            )
        if not ph and not qh:
            if p1[0] != q1[0]:
                return False
            return max(min(p1[1], p2[1]), min(q1[1], q2[1])) <= min(
                max(p1[1], p2[1]), max(q1[1], q2[1])
            )
        if ph:
            (hx1, hy), (hx2, _) = p1, p2
            (vx, vy1), (_, vy2) = q1, q2
        else:
            (hx1, hy), (hx2, _) = q1, q2
            (vx, vy1), (_, vy2) = p1, p2
        x, y = vx, hy
        if not (min(hx1, hx2) <= x <= max(hx1, hx2)):
            return False
        if not (min(vy1, vy2) <= y <= max(vy1, vy2)):
            return False
        inside_h = min(hx1, hx2) < x < max(hx1, hx2)
        inside_v = min(vy1, vy2) < y < max(vy1, vy2)
        return not (inside_h and inside_v)

    def _blocked(self, p1: Point, p2: Point, net: object) -> bool:
        if self._hits_foreign_port(p1, p2, net):
            return True
        for other, q1, q2 in self.segs:
            if other is not net and self._conflict(p1, p2, q1, q2):
                return True
        return False

    def ok_all(self, segs: Sequence[Tuple[Point, Point]], net: object) -> bool:
        tmp = list(self.segs)
        for p1, p2 in segs:
            if p1 == p2:
                continue
            for n2, q1, q2 in tmp:
                if n2 is net:
                    continue  # 同一网络内部 T 接是合法且有意的
                if self._conflict(p1, p2, q1, q2):
                    return False
            if self._hits_foreign_port(p1, p2, net):
                return False
            tmp.append((net, p1, p2))
        return True

    def commit(self, segs: Iterable[Tuple[Point, Point]], net: object) -> None:
        for p1, p2 in segs:
            if p1 != p2:
                self.segs.append((net, p1, p2))


def _straight(src: Point, sinks: List[Point], net: object, rt: Router):
    if all(q[1] == src[1] for q in sinks):
        segs = [(src, q) for q in sinks]
        if rt.ok_all(segs, net):
            return segs
    return None


def _l_shape(src: Point, sink: Point, net: object, rt: Router, mode: str):
    """L 形三段线。mode='asc'：拐弯竖线从源点右侧起向右扫描（先源后汇）；
    mode='sink'：从汇点左侧起向左扫描。"""
    if src[1] == sink[1]:
        return [(src, sink)] if rt.ok_all([(src, sink)], net) else None
    if mode == "asc":
        xs = [src[0] + 10 * k for k in range(2, 32)]
        xs += [src[0] - 10 * k for k in range(1, 6)]
    else:
        xs = [sink[0] - 10 * k for k in range(1, 40) if sink[0] - 10 * k > src[0] + 10]
        xs += [sink[0] + 10 * k for k in range(1, 12)]
    for bx in xs:
        segs = [
            (src, (bx, src[1])),
            ((bx, src[1]), (bx, sink[1])),
            ((bx, sink[1]), sink),
        ]
        if rt.ok_all(segs, net):
            return segs
    return None


def _trunk(src: Point, sinks: List[Point], net: object, rt: Router):
    """多汇点干线：源点右侧一条竖线，短水平支线进各汇点。"""
    ys = [src[1]] + [q[1] for q in sinks]
    lo, hi = min(ys), max(ys)
    limit = min(q[0] for q in sinks) - 20
    tx = src[0] + 20
    while tx <= limit:
        segs = [(src, (tx, src[1])), ((tx, lo), (tx, hi))]
        segs += [((tx, q[1]), q) for q in sinks]
        if rt.ok_all(segs, net):
            return segs
        tx += GRID
    return None


def _highway(src: Point, sink: Point, net: object, rt: Router):
    """绕行：源点可能被同行/邻行端口"困住"（右侧同行有他网端口、上下紧贴
    其他元件），所以垂直逃逸 x 先取源点原位，再向右/左偏移扫描；之后走上/
    下高速路横穿，最后落到汇点。"""
    cands: List[int] = []
    for k in range(4, 26):
        cands += [src[1] + 10 * k, src[1] - 10 * k]
    esc: List[int] = [src[0]]
    for k in range(1, 7):
        esc += [src[0] + 10 * k, src[0] - 10 * k]
    for ex in esc:
        for ly in cands:
            if ly < TOP:
                continue
            bx = sink[0] - 20 if sink[0] - 20 > src[0] + 10 else sink[0] + 20
            segs = [
                (src, (ex, src[1])),
                ((ex, src[1]), (ex, ly)),
                ((ex, ly), (bx, ly)),
                ((bx, ly), (bx, sink[1])),
                ((bx, sink[1]), sink),
            ]
            if rt.ok_all(segs, net):
                return segs
    return None


def _force(src: Point, sink: Point) -> List[Tuple[Point, Point]]:
    """所有策略都失败时的兜底（电路本身有问题，lint 会报多驱动等）。"""
    if src[1] == sink[1]:
        return [(src, sink)]
    bx = max(src[0] + 20, min(sink[0] - 10, sink[0] - 10))
    bx = sink[0] - 10 if sink[0] - 10 > src[0] + 10 else src[0] + 20
    return [
        (src, (bx, src[1])),
        ((bx, src[1]), (bx, sink[1])),
        ((bx, sink[1]), sink),
    ]


def _strictly_inside(p: Point, a: Point, b: Point) -> bool:
    """p 是否严格落在线段 ab 内部（不含端点）。"""
    if a[1] == b[1] == p[1] and min(a[0], b[0]) < p[0] < max(a[0], b[0]):
        return True
    if a[0] == b[0] == p[0] and min(a[1], b[1]) < p[1] < max(a[1], b[1]):
        return True
    return False


def merge_segments(
    segs: Sequence[Tuple[Point, Point]]
) -> List[Tuple[Point, Point]]:
    """合并共线重叠/相接的同向线段，去掉重复，让导线数量最少、最整洁。

    关键：Logisim 里两根线只有"一根的端点落在另一根内部"才导通（T 接），
    两根都从同一点中间穿过只是十字交叉、不导通。合并共线线段会抹掉端点，
    可能把原本的 T 接退化成十字——所以合并前先收集所有丁字交点（某线段的
    端点、同时是垂直方向线段的内点），合并后在交点处把长边切开。
    """
    ends = {p for s in segs for p in s}
    junctions = {
        p
        for p in ends
        for q1, q2 in segs
        if p not in (q1, q2) and _strictly_inside(p, q1, q2)
    }
    hs: Dict[int, List[Tuple[int, int]]] = {}
    vs: Dict[int, List[Tuple[int, int]]] = {}
    for (x1, y1), (x2, y2) in segs:
        if y1 == y2:
            hs.setdefault(y1, []).append((min(x1, x2), max(x1, x2)))
        elif x1 == x2:
            vs.setdefault(x1, []).append((min(y1, y2), max(y1, y2)))

    def _merge_runs(runs: List[Tuple[int, int]], cuts: Set[int]) -> List[Tuple[int, int]]:
        runs = sorted(runs)
        merged: List[Tuple[int, int]] = []
        lo, hi = runs[0]
        for a, b in runs[1:]:
            if a <= hi:
                hi = max(hi, b)
            else:
                merged.append((lo, hi))
                lo, hi = a, b
        merged.append((lo, hi))
        # 在丁字交点处切开，保住 T 接端点
        out: List[Tuple[int, int]] = []
        for lo, hi in merged:
            pts = sorted(c for c in cuts if lo < c < hi)
            prev = lo
            for c in pts:
                out.append((prev, c))
                prev = c
            out.append((prev, hi))
        return out

    out: List[Tuple[Point, Point]] = []
    for y, ivs in hs.items():
        cuts = {j[0] for j in junctions if j[1] == y}
        for lo, hi in _merge_runs(ivs, cuts):
            out.append(((lo, y), (hi, y)))
    for x, ivs in vs.items():
        cuts = {j[1] for j in junctions if j[0] == x}
        for lo, hi in _merge_runs(ivs, cuts):
            out.append(((x, lo), (x, hi)))
    return sorted(out)


def route(
    nets: Sequence[Tuple[object, Point, List[Point]]],
    extra_ports: Sequence[Point] = (),
) -> Tuple[List[Tuple[Point, Point]], List[object]]:
    """按美观优先的策略顺序给每个网络布线。

    返回 (合并后的线段列表, 走投无路只能强行的网络 id 列表)。
    强行 = 电路本身有问题（如多驱动），调用方可把这些网络提前重试。
    """
    rt = Router()
    for net, src, sinks in nets:
        rt.add_port(net, [src] + list(sinks))
    for p in extra_ports:
        rt.add_port(object(), [p])
    out: List[Tuple[Point, Point]] = []
    failed: List[object] = []
    for net, src, sinks in nets:
        cand: Optional[List[Tuple[Point, Point]]] = _straight(src, sinks, net, rt)
        if cand is None and len(sinks) > 1:
            cand = _trunk(src, sinks, net, rt)
        if cand is None:
            cand = []
            ok = True
            for q in sorted(sinks, key=lambda p: (p[1], p[0])):
                sub = None
                for mode in ("asc", "sink"):
                    sub = _l_shape(src, q, net, rt, mode)
                    if sub:
                        break
                if not sub:
                    sub = _highway(src, q, net, rt)
                if not sub:
                    ok = False
                    break
                cand += sub
            if not ok:
                cand = None
        if cand is None:
            failed.append(net)
            cand = []
            for q in sinks:
                cand += _force(src, q)
        rt.commit(cand, net)
        out += cand
    return merge_segments(out), failed
