"""电路结构检查（lint）。

只报告可以确定的问题：重复/缺失标签、悬空导线、未驱动输出、多驱动、
未被连接的逻辑门输入等。对于端口几何未知的元件，会跳过相关推断而不是误报。
"""

from __future__ import annotations

from typing import Dict, List

from .circ import Circuit, Project
from .geometry import is_sequential, ports_of
from .nets import build_netlist


def _issue(severity: str, code: str, message: str, loc=None, comp: str = "") -> Dict[str, object]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "component": comp,
        "loc": list(loc) if loc else None,
    }


def lint_circuit(project: Project, circuit: Circuit) -> Dict[str, object]:
    issues: List[Dict[str, object]] = []
    netlist = build_netlist(circuit)

    # 标签检查
    labels: Dict[str, int] = {}
    for c in circuit.comps:
        if c.name == "Pin":
            if not c.label:
                issues.append(_issue("warning", "pin-no-label", "引脚没有标签，测试向量无法引用它", c.loc, c.name))
            else:
                labels[c.label] = labels.get(c.label, 0) + 1
    for label, n in labels.items():
        if n > 1:
            issues.append(_issue("error", "duplicate-label", f"标签 {label!r} 被 {n} 个引脚重复使用"))

    # 子电路引用
    for c in circuit.comps:
        if c.lib in (None, "proj") and c.name not in ("Pin",) and c.name not in project.circuits:
            known = {
                "AND Gate", "OR Gate", "NOT Gate", "NAND Gate", "NOR Gate", "XOR Gate",
                "XNOR Gate", "Buffer", "Clock", "Constant", "Tunnel", "Splitter", "Junction",
                "LED", "Button", "Probe", "Pin", "D Flip-Flop", "Register", "Counter",
            }
            if c.name not in known:
                issues.append(
                    _issue("warning", "missing-subcircuit", f"找不到子电路 {c.name!r}", c.loc, c.name)
                )

    # 悬空导线
    for pt in netlist.dangling_endpoints:
        issues.append(_issue("warning", "dangling-wire", f"导线端点 {pt} 没有连接到任何元件或导线", pt))

    # 网络驱动检查
    for net in netlist.nets:
        drivers = net.drivers
        sinks = net.sinks
        # Splitter 所在网络：各分支驱动不同的比特位，多驱动不算短路
        via_splitter = any(c.name == "Splitter" for c, _ in net.ports)
        if len(drivers) > 1 and not via_splitter:
            issues.append(
                _issue(
                    "warning",
                    "multiple-drivers",
                    f"网络 {net.label or net.points[0]} 上有 {len(drivers)} 个驱动源（可能短路）",
                    net.points[0],
                )
            )
        if sinks and not drivers and not net.tunnel:
            first = sinks[0]
            issues.append(
                _issue(
                    "warning",
                    "no-driver",
                    f"网络 {net.label or net.points[0]} 只有输入没有驱动源",
                    first[1].loc,
                    first[0].name,
                )
            )

    # 门输入悬空
    for c in circuit.comps:
        ports = ports_of(c)
        if not ports:
            continue
        for p in ports:
            if p.direction != "input":
                continue
            net = netlist.net_at(p.loc)
            if net is None or not net.ports:
                issues.append(
                    _issue("warning", "unconnected-input", f"{c.name} 的输入端口 {p.hint or p.index} 未连接", p.loc, c.name)
                )

    # 统计与提示
    sequential = [c for c in circuit.comps if is_sequential(c)]
    if sequential:
        issues.append(
            _issue(
                "info",
                "sequential",
                "电路包含时序元件：" + "、".join(sorted({c.name for c in sequential})) + "，真值表枚举不适用，请用 circuit_simulate 提供序列",
            )
        )
    if netlist.unsupported:
        names = sorted({c.name for c in netlist.unsupported})
        issues.append(
            _issue("info", "unsupported-geometry", "以下元件的端口几何未知，已跳过连通性推断：" + "、".join(names))
        )

    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = sum(1 for i in issues if i["severity"] == "warning")
    return {
        "circuit": circuit.name,
        "summary": {"errors": errors, "warnings": warnings, "infos": len(issues) - errors - warnings},
        "issues": issues,
        "stats": {
            "components": len(circuit.comps),
            "wires": len(circuit.wires),
            "nets": len(netlist.nets),
            "pins": len(circuit.pins()),
        },
    }
