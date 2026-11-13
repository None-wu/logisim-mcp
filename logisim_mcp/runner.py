"""调用 Logisim-evolution 的无头 test-vector 引擎。

为什么要走 test-vector 而不是自己做逻辑仿真：
  * 这是官方提供的命令行能力（--test-vector），语义与界面仿真完全一致；
  * 支持子电路、总线、算术元件、时序元件；
  * 不需要 TTY 元件、不需要图形界面。

取"实际输出值"的小技巧：把期望值全部写成 0，那么输出为 0 的行会静默通过，
输出非 0 的行会把实际值打印出来。于是：打印了就用打印值，没打印就是 0。
这样一次进程即可拿到整张真值表。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

IS_WINDOWS = sys.platform.startswith("win")
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

_DETAIL_RE = re.compile(r"^\s+(\S+) = (\S+) \(expected (\S+)\)\s*$")
_ROW_RE = re.compile(r"^(\d+)$")
_SUMMARY_RE = re.compile(r"Passed\s*[:：]\s*(\d+)\s*,\s*Failed\s*[:：]\s*(\d+)")


class LogisimNotFound(RuntimeError):
    pass


@dataclass
class RunResult:
    ok: bool
    rows: Dict[int, Dict[str, str]] = field(default_factory=dict)
    passed: int = 0
    failed: int = 0
    error: str = ""
    stdout: str = ""
    stderr: str = ""

    def value(self, row: int, column: str, default: str = "0") -> str:
        return self.rows.get(row, {}).get(column, default)


@dataclass
class Runtime:
    kind: str  # "exe" | "jar"
    path: Path
    java: Optional[str] = None

    def command(self) -> List[str]:
        if self.kind == "jar":
            return [self.java or "java", "-jar", str(self.path)]
        return [str(self.path)]

    def as_dict(self) -> Dict[str, object]:
        return {"kind": self.kind, "path": str(self.path), "java": self.java}


def _config_path() -> Path:
    return Path.home() / ".logisim-mcp.json"


_SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".cache", ".venv", "venv", "AppData", "Windows"}


def _scan_roots(roots: List[Path], out: List[Runtime], depth: int = 3, limit: int = 6) -> None:
    """在若干根目录下做**限深**遍历，收集 logisim 可执行文件与 jar。

    限深 + 跳过 node_modules/.git 之类目录，避免把用户工程目录整体扫一遍；
    同时限制最多检查条目数，防止 cwd 恰好在磁盘根目录时卡住。
    """
    budget = 20000
    found_exe = 0
    found_jar = 0
    for root in roots:
        try:
            if not root.is_dir() or root.parent == root:  # 盘符根目录直接跳过
                continue
        except OSError:
            continue
        base = len(root.parts)
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                budget -= 1
                if budget <= 0 or (found_exe >= limit and found_jar >= limit):
                    return
                cur = Path(dirpath)
                if len(cur.parts) - base >= depth:
                    dirnames[:] = []
                else:
                    dirnames[:] = [
                        d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
                    ]
                for name in sorted(filenames):
                    low = name.lower()
                    if low.endswith(".exe") and "logisim" in low:
                        out.append(Runtime("exe", cur / name))
                        found_exe += 1
                    elif low.endswith(".jar") and "logisim" in low:
                        out.append(Runtime("jar", cur / name))
                        found_jar += 1
        except OSError:
            continue


def _candidates() -> List[Runtime]:
    out: List[Runtime] = []

    env_exe = os.environ.get("LOGISIM_EXE")
    if env_exe:
        p = Path(env_exe)
        if p.exists():
            out.append(Runtime("exe", p))

    try:
        cfg = json.loads(_config_path().read_text(encoding="utf-8"))
        if isinstance(cfg, dict) and cfg.get("exe"):
            p = Path(str(cfg["exe"]))
            if p.exists():
                out.append(Runtime("exe", p))
    except Exception:
        pass

    # 搜索顺序：包自身附近 → 当前工作目录及其上两级。
    # 后者对 npm 全局安装很关键：包在 node_modules 里，Logisim 却在别处。
    here = Path(__file__).resolve().parent.parent
    cwd = Path.cwd()
    search_roots: List[Path] = []
    for root in (here, here.parent, here.parent.parent, cwd, cwd.parent, cwd.parent.parent):
        if root not in search_roots:
            search_roots.append(root)
    _scan_roots(search_roots, out)

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    for p in (
        Path(program_files) / "logisim-evolution" / "logisim-evolution.exe",
        Path(program_files) / "Logisim-evolution" / "Logisim-evolution.exe",
        Path("/Applications/Logisim-evolution.app/Contents/MacOS/logisim-evolution"),
        Path("/usr/bin/logisim-evolution"),
        Path("/usr/local/bin/logisim-evolution"),
    ):
        if p.exists():
            out.append(Runtime("exe", p))

    for name in ("logisim-evolution", "logisim-evolution.exe", "logisim"):
        found = shutil.which(name)
        if found:
            out.append(Runtime("exe", Path(found)))
            break
    return out


def find_runtime(explicit: Optional[str] = None) -> Runtime:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise LogisimNotFound(f"指定的 Logisim 路径不存在: {p}")
        return Runtime("exe" if p.suffix.lower() == ".exe" else "jar", p)

    seen = set()
    for rt in _candidates():
        key = str(rt.path)
        if key in seen:
            continue
        seen.add(key)
        if rt.kind == "exe":
            return rt
    # 没有 exe 时退回 jar（要求 PATH 中有 Java 21+）
    for rt in _candidates():
        if rt.kind == "jar":
            java = shutil.which("java")
            if java:
                return Runtime("jar", rt.path, java)
    raise LogisimNotFound(
        "未找到 Logisim-evolution。请安装官方发行版，或设置环境变量 LOGISIM_EXE 指向可执行文件，"
        "或在 ~/.logisim-mcp.json 写入 {\"exe\": \"C:/path/to/logisim-evolution.exe\"}"
    )


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp1252", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def version_of(rt: Runtime) -> str:
    try:
        p = subprocess.run(
            rt.command() + ["-v"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=90,
            creationflags=_NO_WINDOW,
        )
        text = _decode(p.stdout) + _decode(p.stderr)
        for line in text.splitlines():
            if "Logisim-evolution" in line:
                return line.strip()
        return text.strip().splitlines()[0] if text.strip() else "unknown"
    except Exception as exc:  # pragma: no cover - 环境相关
        return f"unknown ({exc})"


def write_vector(path: Path, header: List[str], rows: List[List[str]]) -> None:
    lines = [" ".join(header)]
    for r in rows:
        lines.append(" ".join(r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_vector_file(
    rt: Runtime,
    circ_file: str | Path,
    circuit: str,
    vector_text: str,
    timeout: int = 180,
    cwd: Optional[str | Path] = None,
) -> RunResult:
    circ_file = Path(circ_file).resolve()
    tmpdir = Path(cwd) if cwd else Path(tempfile.mkdtemp(prefix="logisim-mcp-"))
    vec = tmpdir / "vectors.txt"
    vec.write_text(vector_text, encoding="utf-8")
    cmd = rt.command() + ["-o", "en", "-w", circuit, str(vec), str(circ_file)]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            cwd=str(tmpdir),
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            ok=False,
            error=(
                f"仿真超时（>{timeout}s）。若是自行构造的电路，检查是否含 Junction 元件"
                "（Logisim 5.0.0 无头模式对其存在卡死缺陷），或存在组合环路。"
            ),
        )
    except OSError as exc:
        return RunResult(ok=False, error=f"无法启动 Logisim: {exc}")

    stdout = _decode(proc.stdout)
    stderr = _decode(proc.stderr)
    result = parse_output(stdout, stderr)

    if "Error loading test vector" in stdout or "Error loading test vector" in stderr:
        result.ok = False
    if "has no matching pin" in stdout + stderr:
        result.ok = False
    if "Error preparing test vector" in stdout + stderr:
        result.ok = False
    return result


def normalize(
    rt: Runtime, src: str | Path, dst: str | Path, timeout: int = 120
) -> Tuple[bool, str]:
    """用官方 `-n` 重新加载并另存，等价于"GUI 打开一次再保存"。

    这是校验生成文件"在界面里能完整加载"的最强证据：Logisim 会按自己的规则
    重写工程，凡是它不认识/丢失的东西都会在这个结果里消失或被改写。
    """
    src = Path(src).resolve()
    dst = Path(dst)
    if dst.exists():
        dst.unlink()
    cmd = rt.command() + ["-o", "en", "-n", str(src), str(dst)]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return False, f"规范化超时（>{timeout}s）"
    except OSError as exc:
        return False, f"无法启动 Logisim: {exc}"

    text = _decode(proc.stdout) + _decode(proc.stderr)
    bad = [
        line.strip()
        for line in text.replace("\r", "\n").split("\n")
        if line.strip().startswith(("Error", "Exception", "WARNING"))
    ]
    if bad:
        return False, "; ".join(dict.fromkeys(bad))[:600]
    if not dst.exists() or dst.stat().st_size == 0:
        return False, "Logisim 没有产出文件（加载失败）"
    return True, ""


def parse_output(stdout: str, stderr: str) -> RunResult:
    text = stdout.replace("\r\n", "\n").replace("\r", "\n")
    rows: Dict[int, Dict[str, str]] = {}
    current: Optional[int] = None
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        m = _ROW_RE.match(stripped)
        if m:
            current = int(m.group(1))
            rows.setdefault(current, {})
            continue
        m = _DETAIL_RE.match(line)
        if m and current is not None:
            rows[current][m.group(1)] = m.group(2)
            continue

    passed = failed = 0
    m = _SUMMARY_RE.search(text)
    if m:
        passed, failed = int(m.group(1)), int(m.group(2))

    errors = []
    for line in (stdout + "\n" + stderr).replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("Error") and not s.startswith("Error on test vector"):
            errors.append(s)
        elif "has no matching pin" in s or "Test Vector data format error" in s:
            errors.append(s)

    return RunResult(
        ok=not errors,
        rows=rows,
        passed=passed,
        failed=failed,
        error="; ".join(dict.fromkeys(errors))[:800],
        stdout=stdout,
        stderr=stderr,
    )


def format_value(value: int, width: int) -> str:
    """把整数转成 test-vector 需要的二进制字面量（必须恰好 width 位）。"""
    if width <= 1:
        return "1" if value & 1 else "0"
    value &= (1 << width) - 1
    return format(value, f"0{width}b")


def parse_value(text: str) -> int:
    """接受 12 / 0b1100 / 0x0c / '1100' / true / false。

    纯 0/1 串按二进制解析（数字电路场景更符合直觉），其余按十进制。
    """
    s = str(text).strip().replace("_", "")
    low = s.lower()
    if low in ("true", "t", "yes"):
        return 1
    if low in ("false", "f", "no"):
        return 0
    if low.startswith("0b"):
        return int(low[2:], 2)
    if low.startswith("0x"):
        return int(low[2:], 16)
    if low.startswith("0o"):
        return int(low[2:], 8)
    if re.fullmatch(r"[01]+", low):
        return int(low, 2)
    return int(low, 10)
