"""命令行入口：stdio / HTTP / 自检。"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import tools_impl
from .mcp import SERVER_NAME, SERVER_VERSION, TOOLS, McpServer, serve_stdio


def _serve_http(host: str, port: int) -> None:
    server = McpServer()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 静默，避免污染 MCP 日志
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "content-type, mcp-session-id")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # noqa: N802
            self._send(204, b"")

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") in ("/health", ""):
                body = json.dumps(
                    {"name": SERVER_NAME, "version": SERVER_VERSION, "status": "ok"}, ensure_ascii=False
                ).encode("utf-8")
                self._send(200, body)
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") not in ("/mcp", ""):
                self._send(404, b'{"error":"not found"}')
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                message = json.loads(raw.decode("utf-8"))
            except Exception:
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
                ).encode("utf-8")
                self._send(400, body)
                return
            if isinstance(message, list):
                payload = [r for r in (server.handle(m) for m in message) if r]
            else:
                payload = server.handle(message)
            if payload is None:
                self._send(202, b"")
                return
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"[{SERVER_NAME}] HTTP listening on http://{host}:{port}/mcp", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def _retrofit(argv) -> int:
    parser = argparse.ArgumentParser(prog="logisim-mcp retrofit", description="升级 .circ 工程骨架")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    parser.add_argument("--no-verify", action="store_true", help="跳过 Logisim 重新加载校验")
    parser.add_argument("--output", help="写到指定文件（仅单个输入时可用）")
    args = parser.parse_args(argv)
    if args.output and len(args.paths) > 1:
        parser.error("--output 只能配一个输入文件")

    failed = 0
    for path in args.paths:
        try:
            info = tools_impl.circuit_retrofit(
                path,
                output=args.output,
                write=not args.dry_run,
                verify=not args.no_verify,
            )
        except tools_impl.ToolError as exc:
            print(f"✗ {path}: {exc}")
            failed += 1
            continue
        mark = "→" if info.get("changed") else "="
        print(f"{mark} {info['path']}  ({len(info['libs_before'])} 库 → {len(info['libs_after'])} 库, "
              f"电路 {', '.join(info['circuits'])}, 加载: {info.get('logisim_load', 'skipped')})")
        if info.get("lib_remap"):
            print(f"  库索引重映射: {info['lib_remap']}")
        for w in info.get("warnings", []):
            print(f"  ! {w}")
        if info.get("logisim_load", "").startswith("failed"):
            failed += 1
    return 0 if failed == 0 else 1


def _doctor() -> int:
    info = tools_impl.logisim_info()
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(f"\ntools ({len(TOOLS)}): " + ", ".join(t["name"] for t in TOOLS))
    return 0 if info.get("found") else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "retrofit":
        return _retrofit(argv[1:])

    parser = argparse.ArgumentParser(description="Logisim-evolution MCP server")
    parser.add_argument("--http", nargs="?", const=8765, type=int, metavar="PORT", help="以 HTTP 方式运行")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--doctor", action="store_true", help="打印环境自检信息后退出")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{SERVER_NAME} {SERVER_VERSION}")
        return 0
    if args.doctor:
        return _doctor()
    if args.http:
        _serve_http(args.host, args.http)
        return 0
    serve_stdio()
    return 0
