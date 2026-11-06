#!/usr/bin/env python3
"""Logisim-evolution MCP server 入口（兼容旧版直接运行方式）。

    python server.py                 # stdio（默认）
    python server.py --http 8765     # Streamable HTTP: http://127.0.0.1:8765/mcp
    python server.py --doctor        # 环境自检
    python -m logisim_mcp            # 等价用法
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logisim_mcp.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
