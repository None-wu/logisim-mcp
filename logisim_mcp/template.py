"""Logisim-evolution 5.0.0 原生工程骨架：元件库表 / 工具栏 / 快捷键 / 选项。

为什么必须有这一层：
  * ``<lib>`` 列表决定 GUI 左侧"元件库"面板里出现哪些库。Logisim **不会**在
    加载时自动补全缺失的库，所以只写 4 个库的文件打开后，面板里就只剩 4 个库，
    用户会觉得"功能少了"（找不到算术、存储、TTL、Soc 等元件）。
  * ``<toolbar>`` 决定窗口顶部快捷工具条。空 ``<toolbar/>`` 会得到一条空工具条。
  * ``<mappings>`` 是鼠标按键绑定，缺省时部分快捷操作（右键菜单）不可用。

这里的内容直接对齐 5.0.0 自己"新建工程 → 保存"产出的文件（含库名编号规则：
库名是十进制序号字符串，与 4.x 的十六进制字母编号不同）。
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

# 库名（<lib name>）——5.0.0 原生写法是十进制序号
LIB_WIRING = "0"
LIB_GATES = "1"
LIB_PLEXERS = "2"
LIB_ARITHMETIC = "3"
LIB_MEMORY = "4"
LIB_IO = "5"
LIB_TCL = "6"
LIB_BASE = "7"
LIB_BFH = "8"
LIB_TTL = "9"
LIB_IO_EXTRA = "10"
LIB_SOC = "11"
LIB_FP = "12"

# 默认新建工程携带的全部内置库（顺序即 GUI 面板顺序）
DEFAULT_LIBS: List[Tuple[str, str]] = [
    ("#Wiring", LIB_WIRING),
    ("#Gates", LIB_GATES),
    ("#Plexers", LIB_PLEXERS),
    ("#Arithmetic", LIB_ARITHMETIC),
    ("#Memory", LIB_MEMORY),
    ("#I/O", LIB_IO),
    ("#TCL", LIB_TCL),
    ("#Base", LIB_BASE),
    ("#BFH-Praktika", LIB_BFH),
    ("#TTL", LIB_TTL),
    ("#Input/Output-Extra", LIB_IO_EXTRA),
    ("#Soc", LIB_SOC),
    ("#FPArithmetic", LIB_FP),
]

DESC_TO_NAME = dict(DEFAULT_LIBS)
LIB_ORDER = [desc for desc, _ in DEFAULT_LIBS]

PROVENANCE = (
    "  This file is intended to be loaded by Logisim-evolution "
    "v5.0.0(https://github.com/logisim-evolution/)."
)

# 顶部快捷工具条（与 GUI 新建工程一致；5.0 的 <tool> 不再写 lib 属性）
TOOLBAR: List[str] = [
    "  <toolbar>",
    '    <tool name="Poke Tool"/>',
    '    <tool name="Edit Tool"/>',
    '    <tool name="Wiring Tool"/>',
    '    <tool name="Text Tool"/>',
    '    <tool name="Image"/>',
    "    <sep/>",
    '    <tool name="Pin"/>',
    "    <tool name=\"Pin\">",
    '      <a name="facing" val="west"/>',
    '      <a name="type" val="output"/>',
    "    </tool>",
    "    <sep/>",
    '    <tool name="NOT Gate"/>',
    '    <tool name="AND Gate"/>',
    '    <tool name="OR Gate"/>',
    '    <tool name="XOR Gate"/>',
    '    <tool name="NAND Gate"/>',
    '    <tool name="NOR Gate"/>',
    "    <sep/>",
    '    <tool name="D Flip-Flop"/>',
    '    <tool name="Register"/>',
    "  </toolbar>",
]

# 鼠标按键绑定（右键菜单 / 属性菜单）
MAPPINGS: List[str] = [
    "  <mappings>",
    '    <tool map="Button2" name="Poke Tool"/>',
    '    <tool map="Button3" name="Menu Tool"/>',
    '    <tool map="Ctrl Button1" name="Menu Tool"/>',
    "  </mappings>",
]

# 只写非默认值，与原生保存一致
OPTIONS: List[str] = [
    "  <options>",
    '    <a name="gateUndefined" val="ignore"/>',
    '    <a name="simlimit" val="1000"/>',
    '    <a name="simrand" val="0"/>',
    "  </options>",
]

# 5.0 会在 #Wiring 库里记录 Pin 工具的默认外观
WIRING_LIB_BLOCK: List[str] = [
    '  <lib desc="#Wiring" name="0">',
    '    <tool name="Pin">',
    '      <a name="appearance" val="classic"/>',
    "    </tool>",
    "  </lib>",
]


def lib_lines(extra: Sequence[Tuple[str, str]] = ()) -> List[str]:
    """库表行。extra 用于保留用户自定义/插件库（desc, name）。"""
    lines: List[str] = []
    for desc, name in DEFAULT_LIBS:
        if desc == "#Wiring":
            lines.extend(WIRING_LIB_BLOCK)
        else:
            lines.append(f'  <lib desc="{desc}" name="{name}"/>')
    for desc, name in extra:
        lines.append(f'  <lib desc="{desc}" name="{name}"/>')
    return lines


def project_head(name: str, extra_libs: Sequence[Tuple[str, str]] = ()) -> List[str]:
    """从 XML 声明一直到 <circuit> 之前的所有行。"""
    return [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        '<project source="5.0.0" version="1.0">',
        PROVENANCE,
        "",
        *lib_lines(extra_libs),
        f'  <main name="{name}"/>',
        *OPTIONS,
        *MAPPINGS,
        *TOOLBAR,
    ]


def circuit_attrs(name: str) -> List[str]:
    """<circuit> 内部的工程级属性（原生保存会写这两项）。"""
    return [
        f'    <a name="circuit" val="{name}"/>',
        '    <a name="simulationFrequency" val="1.0"/>',
    ]


def next_lib_name(used: Iterable[str]) -> str:
    """给未知库分配一个未被占用的序号名。"""
    taken = {str(u) for u in used}
    i = len(DEFAULT_LIBS)
    while str(i) in taken:
        i += 1
    return str(i)


def is_default_lib(desc: Optional[str]) -> bool:
    return bool(desc) and desc in DESC_TO_NAME
