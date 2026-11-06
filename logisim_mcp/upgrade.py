"""把已有 .circ 升级为 Logisim-evolution 5.0.0 的原生工程骨架。

痛点：文件里 ``<lib>`` 声明得少、``<toolbar/>`` 为空时，GUI 打开后会看到
"元件库面板只有几个库、顶部工具条空白"，因为 Logisim 不会在加载时补全这些。

做法：以 ``<lib desc=...>`` 为准建立"旧库名 → 原生库名"映射（顺带修正 4.x 的
十六进制库名 A/B/C/D 与 5.0 十进制编号的错位），重写工程头（全量内置库、
Pin 默认外观、默认工具栏、鼠标绑定、选项），``<circuit>`` 内容原样保留。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import runner, template
from .circ import load_project

_NAME_RE = re.compile(r'name="([^"]+)"')


def _esc(v: object) -> str:
    return (
        str(v)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _ser(el: ET.Element, indent: int) -> List[str]:
    pad = " " * indent
    attrs = "".join(f' {k}="{_esc(v)}"' for k, v in el.attrib.items())
    if len(el) == 0:
        return [f"{pad}<{el.tag}{attrs}/>"]
    lines = [f"{pad}<{el.tag}{attrs}>"]
    for child in el:
        lines.extend(_ser(child, indent + 2))
    lines.append(f"{pad}</{el.tag}>")
    return lines


def _collect_libs(root: ET.Element) -> List[Tuple[str, str]]:
    return [(el.get("desc", ""), el.get("name", "")) for el in root.findall("lib")]


def _build_remap(libs: List[Tuple[str, str]]) -> Tuple[Dict[str, str], List[Tuple[str, str]]]:
    remap: Dict[str, str] = {}
    extras: List[Tuple[str, str]] = []
    used = set(template.DESC_TO_NAME.values())
    for desc, name in libs:
        if desc in template.DESC_TO_NAME:
            remap[name] = template.DESC_TO_NAME[desc]
            continue
        # 非内置库（自定义 jar / 插件库）：尽量保留原名，冲突时分配新号
        new_name = name if name and name not in used else template.next_lib_name(used)
        used.add(new_name)
        remap[name] = new_name
        extras.append((desc, new_name))
    return remap, extras


def _options_lines(root: ET.Element) -> List[str]:
    existing = [(a.get("name", ""), a.get("val", "")) for a in root.findall("options/a")]
    seen = {n for n, _ in existing}
    lines = ["  <options>"]
    for name, val in existing:
        lines.append(f'    <a name="{name}" val="{val}"/>')
    for line in template.OPTIONS[1:-1]:
        m = _NAME_RE.search(line)
        if m and m.group(1) not in seen:
            lines.append(line)
            seen.add(m.group(1))
    lines.append("  </options>")
    return lines


def retrofit_xml(path: str | Path) -> Tuple[str, Dict[str, object]]:
    """返回 (升级后的完整 XML 文本, 变更摘要)。"""
    p = Path(path)
    root = ET.parse(p).getroot()
    if root.tag != "project":
        raise ValueError(f"{p.name} 不是 Logisim 工程文件（根元素 <{root.tag}>）")

    libs = _collect_libs(root)
    remap, extras = _build_remap(libs)

    circuits = root.findall("circuit")
    if not circuits:
        raise ValueError(f"{p.name} 中没有任何电路")
    main_el = root.find("main")
    main = (main_el.get("name") if main_el is not None else None) or circuits[0].get("name", "main")

    unknown_libs = set()
    for comp in root.iter("comp"):
        old = comp.get("lib")
        if old is None:
            continue
        if old in remap:
            comp.set("lib", remap[old])
        else:
            unknown_libs.add(old)

    warnings: List[str] = []
    if unknown_libs:
        warnings.append(
            "以下元件引用了未声明的库索引，未做重映射："
            + ", ".join(sorted(unknown_libs))
            + "（该文件可能本身不完整）"
        )

    lines: List[str] = []
    lines.extend(template.project_head(main, extras))
    for c in circuits:
        name = c.get("name", "")
        have = {a.get("name") for a in c.findall("a")}
        lines.append(f'  <circuit name="{name}">')
        for attr_line in template.circuit_attrs(name):
            attr_name = _NAME_RE.search(attr_line).group(1)  # type: ignore[union-attr]
            if attr_name not in have:
                lines.append(attr_line)
        for sub in c:
            lines.extend(_ser(sub, 4))
        lines.append("  </circuit>")
    lines.append("</project>")
    lines.append("")

    info: Dict[str, object] = {
        "path": str(p),
        "libs_before": [desc or name for desc, name in libs],
        "libs_after": [desc for desc, _ in template.DEFAULT_LIBS] + [d for d, _ in extras],
        "lib_remap": {k: v for k, v in remap.items() if k != v},
        "circuits": [c.get("name", "") for c in circuits],
        "main": main,
        "warnings": warnings,
    }
    return "\n".join(lines), info


def retrofit(
    path: str | Path,
    write: bool = True,
    verify: bool = True,
    output: Optional[str | Path] = None,
) -> Dict[str, object]:
    """升级工程骨架；write=False 时只报告将要做的改动。"""
    p = Path(path)
    xml, info = retrofit_xml(p)
    original = p.read_text(encoding="utf-8", errors="replace")
    info["changed"] = xml.strip() != original.strip()
    if not write:
        return info

    target = Path(output) if output else p
    target.write_text(xml, encoding="utf-8")
    info["path"] = str(target)
    load_project(target)  # 结构自检

    if verify:
        try:
            rt = runner.find_runtime()
        except runner.LogisimNotFound as exc:
            info["logisim_load"] = f"skipped ({exc})"
        else:
            tmp = target.with_suffix(".verify.circ")
            ok, err = runner.normalize(rt, target, tmp)
            info["logisim_load"] = "ok" if ok else f"failed: {err}"
            if tmp.exists():
                tmp.unlink()
    return info
