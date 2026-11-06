"""真值表枚举与布尔最小化（Quine–McCluskey）。"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Dict, List, Optional, Sequence, Tuple

from .circ import Circuit, Project
from .runner import Runtime, format_value, run_vector_file

MAX_DEFAULT_ROWS = 1024


@dataclass
class PinInfo:
    label: str
    width: int


def signal_pins(circuit: Circuit) -> Tuple[List[PinInfo], List[PinInfo], List[str]]:
    """返回 (输入引脚, 输出引脚, 被忽略的无标签引脚名)。"""
    ins, outs, skipped = [], [], []
    for c in circuit.comps:
        if c.name != "Pin":
            continue
        label = c.label
        if not label:
            skipped.append(f"Pin@{c.loc}")
            continue
        (outs if c.attr("type", "input") == "output" else ins).append(PinInfo(label, c.width))
    ins.sort(key=lambda p: p.label)
    outs.sort(key=lambda p: p.label)
    return ins, outs, skipped


def _header(ins: Sequence[PinInfo], outs: Sequence[PinInfo]) -> List[str]:
    def col(p: PinInfo) -> str:
        return f"{p.label}[{p.width}]" if p.width > 1 else p.label

    return [col(p) for p in ins] + [col(p) for p in outs]


def _decode(text: str, width: int):
    t = text.strip()
    if not t:
        return 0
    if all(ch in "01" for ch in t):
        return int(t, 2)
    if t.lower().startswith("0x"):
        try:
            return int(t[2:], 16)
        except ValueError:
            return t
    return t


def evaluate_rows(
    rt: Runtime,
    project: Project,
    circuit: Circuit,
    assignments: List[Dict[str, object]],
    timeout: int = 180,
    derived: Optional[Dict[str, Dict[str, object]]] = None,
) -> Tuple[List[Dict[str, object]], List[PinInfo], List[PinInfo], str]:
    """对若干组输入赋值求实际输出（不校验，只读取）。

    derived: Splitter 兼容层的派生列描述（见 splitter_compat.derived_spec）。
    assignments 只需包含用户输入；派生列自动按位合成/拆分。"""
    from . import splitter_compat

    ins_all, outs, _ = signal_pins(circuit)
    if derived:
        ins = [p for p in ins_all if p.label not in derived]
    else:
        ins = ins_all
    header = _header(ins_all, outs)
    rows: List[List[str]] = []
    for a in assignments:
        user_vals: Dict[str, int] = {}
        for p in ins:
            if p.label not in a:
                raise ValueError(f"缺少输入 {p.label!r} 的取值")
            user_vals[p.label] = (
                int(a[p.label]) if isinstance(a[p.label], (int, bool)) else splitter_compat._as_int(a[p.label])
            )
        all_vals = dict(user_vals)
        if derived:
            try:
                all_vals.update(splitter_compat.derived_values(derived, user_vals))
            except splitter_compat.CompatError as exc:
                raise ValueError(str(exc)) from exc
        row = [format_value(all_vals[p.label], p.width) for p in ins_all]
        for p in outs:
            row.append("0" * p.width if p.width > 1 else "0")
        rows.append(row)

    text = " ".join(header) + "\n" + "\n".join(" ".join(r) for r in rows) + "\n"
    res = run_vector_file(rt, project.path, circuit.name, text, timeout=timeout)
    if not res.ok:
        raise RuntimeError(res.error or "Logisim 运行失败")

    decoded: List[Dict[str, object]] = []
    for i in range(1, len(assignments) + 1):
        out_row: Dict[str, object] = {}
        for p in outs:
            raw = res.value(i, p.label, "0" * p.width if p.width > 1 else "0")
            out_row[p.label] = _decode(raw, p.width)
        decoded.append(out_row)
    return decoded, ins, outs, res.stdout


def truth_table(
    rt: Runtime,
    project: Project,
    circuit: Circuit,
    max_rows: int = MAX_DEFAULT_ROWS,
    derived: Optional[Dict[str, Dict[str, object]]] = None,
) -> Dict[str, object]:
    ins_all, outs, skipped = signal_pins(circuit)
    if derived:
        ins = [p for p in ins_all if p.label not in derived]
    else:
        ins = ins_all
    if not ins:
        raise ValueError("该电路没有带标签的输入引脚，无法枚举真值表")
    if not outs:
        raise ValueError("该电路没有带标签的输出引脚，无法枚举真值表")

    total = 1
    for p in ins:
        total <<= p.width
    if total > max_rows:
        raise ValueError(
            f"输入组合共 {total} 行，超过上限 {max_rows}；请用 circuit_simulate 指定输入，"
            f"或调大 max_rows（谨慎）"
        )

    assignments: List[Dict[str, object]] = []
    for combo in product(*[range(1 << p.width) for p in ins]):
        assignments.append({p.label: v for p, v in zip(ins, combo)})

    outputs, _, _, _ = evaluate_rows(rt, project, circuit, assignments, derived=derived)

    table = []
    for a, o in zip(assignments, outputs):
        table.append({"in": a, "out": o})

    result: Dict[str, object] = {
        "circuit": circuit.name,
        "inputs": [{"name": p.label, "width": p.width} for p in ins],
        "outputs": [{"name": p.label, "width": p.width} for p in outs],
        "row_count": len(table),
        "rows": table,
        "skipped_pins": skipped,
    }

    # 组合逻辑最小化：仅在输入全部为 1 bit 时有意义（多比特总线不能当作单个布尔变量）
    if any(p.width > 1 for p in ins):
        result["expressions_skipped"] = "存在多比特输入，布尔表达式推导不适用；请直接使用 rows"
        return result

    expressions: Dict[str, str] = {}
    bit_expr: Dict[str, List[str]] = {}
    unknown = False
    for p in outs:
        if p.width == 1:
            minterms = [i for i, row in enumerate(table) if row["out"][p.label] == 1]
            dont = [i for i, row in enumerate(table) if not isinstance(row["out"][p.label], int)]
            if dont:
                unknown = True
                continue
            if not minterms:
                expressions[p.label] = "0"
                continue
            if len(minterms) == len(table):
                expressions[p.label] = "1"
                continue
            sop = minimize(minterms, dont, len(ins), [x.label for x in ins])
            expressions[p.label] = sop or "0"
        else:
            bits = []
            for b in range(p.width):
                mts = [
                    i
                    for i, row in enumerate(table)
                    if isinstance(row["out"][p.label], int) and (int(row["out"][p.label]) >> b) & 1
                ]
                if not mts:
                    bits.append("0")
                elif len(mts) == len(table):
                    bits.append("1")
                else:
                    bits.append(minimize(mts, [], len(ins), [x.label for x in ins]) or "0")
            bit_expr[p.label] = bits
    if expressions:
        result["expressions"] = expressions
    if bit_expr:
        result["bit_expressions"] = bit_expr
    if unknown:
        result["warning"] = "部分输出出现 U/E/x 等非 0/1 值，已跳过对应表达式推导"
    return result


# --------------------------------------------------------------------------
# Quine–McCluskey
# --------------------------------------------------------------------------





def _covers(implicant: int, mask: int, minterm: int, nvars: int) -> bool:
    return (minterm & mask) == (implicant & mask)


def minimize(
    minterms: Sequence[int],
    dontcares: Sequence[int],
    nvars: int,
    names: Optional[Sequence[str]] = None,
) -> str:
    """返回最简与或式（SOP），例如 "a·b' + a'·b" 写作 "a&!b | !a&b"。"""
    if nvars > 12:
        return ""
    names = list(names or [chr(ord("a") + i) for i in range(nvars)])
    terms = sorted(set(minterms) | set(dontcares))
    if not terms:
        return ""

    mask_all = (1 << nvars) - 1
    # 每个蕴含项用 (value, mask) 表示，mask 位为 0 表示该位被消除
    current = {(t, mask_all) for t in terms}
    primes = set()
    while current:
        nxt = set()
        used = set()
        items = sorted(current)
        for i, (v1, m1) in enumerate(items):
            for v2, m2 in items[i + 1 :]:
                if m1 != m2:
                    continue
                diff = v1 ^ v2
                if diff and (diff & (diff - 1)) == 0:
                    # 合并：消去的位从 mask 中去掉
                    nxt.add((v1 & ~diff, m1 & ~diff))
                    used.add((v1, m1))
                    used.add((v2, m2))
        primes |= current - used
        current = nxt

    # 只保留覆盖到真实 minterm 的质蕴含项
    minterms_set = set(minterms)
    primes = {p for p in primes if any(_covers(p[0], p[1], m, nvars) for m in minterms_set)}

    # 贪心集合覆盖（先选覆盖最多者，再按字面量少者优先）
    uncovered = set(minterms_set)
    chosen = []
    while uncovered:
        best = None
        best_score = None
        for p in primes:
            cover = {m for m in uncovered if _covers(p[0], p[1], m, nvars)}
            if not cover:
                continue
            score = (len(cover), bin(p[1]).count("1"))
            if best_score is None or score > best_score:
                best, best_score = p, score
        if best is None:
            break
        chosen.append(best)
        uncovered -= {m for m in uncovered if _covers(best[0], best[1], m, nvars)}
        primes.discard(best)

    terms_str = []
    for value, mask in sorted(chosen, key=lambda x: (-bin(x[1]).count("1"), x[0])):
        lits = []
        for i in range(nvars):
            if (mask >> i) & 1:
                bit = (value >> i) & 1
                lits.append(names[i] if bit else f"!{names[i]}")
        terms_str.append("&".join(lits) if lits else "1")
    return " | ".join(terms_str)
