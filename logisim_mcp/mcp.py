"""极简 MCP（Model Context Protocol）服务端实现，零第三方依赖。

支持：
  * stdio 传输（Claude Desktop / Cursor / Cline / Continue / CodeBuddy 等通用）
  * 可选 Streamable HTTP（POST /mcp，无状态 JSON 响应）
协议版本：2025-06-18（同时兼容 2025-03-26 / 2024-11-05 的客户端握手）。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

from . import tools_impl as impl

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_NAME = "logisim-evolution-mcp"
SERVER_VERSION = impl.SERVER_VERSION


def _schema(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


_PATH = {"type": "string", "description": "Path to a .circ project file (absolute, or relative to the server cwd)"}

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "logisim_info",
        "title": "Check Logisim environment",
        "description": (
            "Detect the local Logisim-evolution installation and report its version plus the headless "
            "test-vector engine status. Call this first when anything fails."
        ),
        "inputSchema": _schema({}, []),
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "circuit_list",
        "title": "List circuits in a project",
        "description": (
            "List every circuit (including subcircuits) in a .circ project with component/wire counts and "
            "the labelled input/output pins of each circuit. Start here to discover the main circuit name."
        ),
        "inputSchema": _schema({"path": _PATH}, ["path"]),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "circuit_inspect",
        "title": "Inspect circuit structure",
        "description": (
            "Return the structural model of one circuit: component inventory, pins with bit widths, "
            "per-component attributes and port coordinates, subcircuit references, and optionally the "
            "traced nets (wire connectivity). Use it to understand or review a design before simulating."
        ),
        "inputSchema": _schema(
            {
                "path": _PATH,
                "circuit": {"type": "string", "description": "Circuit name; defaults to the project's main circuit"},
                "include_nets": {"type": "boolean", "default": False},
                "include_ports": {"type": "boolean", "default": True},
            },
            ["path"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "circuit_simulate",
        "title": "Simulate a circuit",
        "description": (
            "Run the real Logisim simulation engine headlessly and return the actual output pin values. "
            "Combinational by default: pass `inputs` as {pinLabel: value}. For sequential circuits pass "
            "`steps`: a list of input assignments executed in order (the circuit is reset first, then each "
            "step advances one clock tick). Values accept 1, 0, '1010', '0x1f', or decimal. "
            "Outputs are returned as decimal integers."
        ),
        "inputSchema": _schema(
            {
                "path": _PATH,
                "circuit": {"type": "string"},
                "inputs": {
                    "type": "object",
                    "description": "Combinational input values, e.g. {\"a\": 1, \"b\": 0}",
                    "additionalProperties": True,
                },
                "steps": {
                    "type": "array",
                    "description": "Sequential steps, each an input mapping; one clock tick per step after the first",
                    "items": {"type": "object", "additionalProperties": True},
                },
                "clock": {"type": "string", "description": "Optional clock pin/component label, informational only"},
            },
            ["path"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "circuit_truth_table",
        "title": "Build truth table and expressions",
        "description": (
            "Enumerate all input combinations of a COMBINATIONAL circuit, simulate each one, and return the "
            "full truth table plus minimized sum-of-products expressions per output (Quine–McCluskey). "
            "Refuses sequential circuits (use circuit_simulate with steps instead)."
        ),
        "inputSchema": _schema(
            {
                "path": _PATH,
                "circuit": {"type": "string"},
                "max_rows": {"type": "integer", "default": 1024, "minimum": 2, "maximum": 65536},
                "include_rows": {"type": "boolean", "default": True},
            },
            ["path"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "circuit_test",
        "title": "Run test vectors",
        "description": (
            "Run a Logisim test-vector file (or inline vector text) against a circuit and report per-row "
            "pass/fail with expected vs actual values. Vector format: first line is the header "
            "(e.g. `a b y[4]`), following lines are whitespace-separated values; binary/decimal/0x are "
            "accepted, `#` starts a comment, and `<DC>` marks a don't-care output."
        ),
        "inputSchema": _schema(
            {
                "path": _PATH,
                "circuit": {"type": "string"},
                "vectors": {"type": "string", "description": "Inline test vector text"},
                "vector_file": {"type": "string", "description": "Path to a .txt test vector file"},
                "max_report": {"type": "integer", "default": 40, "minimum": 1, "maximum": 500},
            },
            ["path"],
        ),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "circuit_create",
        "title": "Create a circuit from a netlist",
        "description": (
            "Generate a valid Logisim 5.x .circ file from a JSON netlist, with automatic layout and "
            "orthogonal wiring, then verify it loads in Logisim. Component types: input, output, and, or, "
            "not, nand, nor, xor, xnor, buffer, clock, led, button, constant. "
            "Netlist shape: {\"circuit\":\"half_adder\",\"components\":[{\"id\":\"a\",\"type\":\"input\","
            "\"label\":\"a\"},{\"id\":\"g1\",\"type\":\"xor\",\"inputs\":2},{\"id\":\"s\",\"type\":\"output\","
            "\"label\":\"sum\"}],\"connections\":[[\"a\",\"g1.in0\"],[\"g1.out\",\"s\"]]}. "
            "Ports are addressed as `id.out`, `id.in0`, `id.in1`, ... Optional `at:[x,y]` overrides layout."
        ),
        "inputSchema": _schema(
            {
                "output": {"type": "string", "description": "Output .circ path"},
                "netlist": {"type": ["object", "string"], "description": "Netlist object (or JSON string)"},
                "smoke_test": {"type": "boolean", "default": True, "description": "Verify Logisim can load it"},
            },
            ["output", "netlist"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "circuit_lint",
        "title": "Lint circuit structure",
        "description": (
            "Static checks on a circuit: duplicate/missing pin labels, dangling wire endpoints, nets without "
            "a driver, multiple drivers, unconnected gate inputs, and missing subcircuit references. "
            "Returns issues with severity and coordinates."
        ),
        "inputSchema": _schema({"path": _PATH, "circuit": {"type": "string"}}, ["path"]),
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "circuit_retrofit",
        "title": "Upgrade project skeleton to native 5.x form",
        "description": (
            "Rewrite the project header of a .circ so the GUI shows everything a hand-made project has: "
            "the full built-in library set in the component panel (#Wiring, #Gates, #Plexers, #Arithmetic, "
            "#Memory, #I/O, #TCL, #Base, #BFH-Praktika, #TTL, #Input/Output-Extra, #Soc, #FPArithmetic), "
            "the default top toolbar, mouse-button bindings and options. Library indices are remapped by "
            "`<lib desc=...>`, so 4.x hex names (A/B/C) and stale indices keep pointing at the right library; "
            "all circuits, components, attributes and wires are preserved. Files that declare only a few "
            "libraries look like they are missing features in the GUI - this fixes that."
        ),
        "inputSchema": _schema(
            {
                "path": _PATH,
                "output": {"type": "string", "description": "Write to this path instead of editing in place"},
                "write": {"type": "boolean", "default": True, "description": "false = dry run"},
                "verify": {
                    "type": "boolean",
                    "default": True,
                    "description": "Re-normalize with Logisim to prove it still loads",
                },
            },
            ["path"],
        ),
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
]

_HANDLERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "logisim_info": impl.logisim_info,
    "circuit_list": impl.circuit_list,
    "circuit_inspect": impl.circuit_inspect,
    "circuit_simulate": impl.circuit_simulate,
    "circuit_truth_table": impl.circuit_truth_table,
    "circuit_test": impl.circuit_test,
    "circuit_create": impl.circuit_create,
    "circuit_lint": impl.circuit_lint,
    "circuit_retrofit": impl.circuit_retrofit,
}

INSTRUCTIONS = """This server exposes Logisim-evolution (digital logic simulator) to an agent.

Typical workflow:
1. logisim_info            -> confirm the local Logisim executable was found.
2. circuit_list            -> discover circuits and pin names of a .circ file.
3. circuit_inspect         -> understand components, widths and wiring.
4. circuit_simulate / circuit_truth_table -> get real simulated outputs and minimized expressions.
5. circuit_create          -> build a new .circ from a JSON netlist (gates, pins, clock, LED).
6. circuit_lint            -> check structure (dangling wires, missing drivers, ...).
7. circuit_retrofit        -> upgrade an old/foreign .circ to the native 5.x project skeleton so the GUI
                              shows the full component library panel and toolbar.

Notes:
- All simulation runs the official Logisim test-vector engine, so semantics match the GUI exactly.
- Pin labels (not component ids) are the signal names used by simulate/truth table.
- Sequential circuits need circuit_simulate with `steps`; truth tables are combinational only.
"""


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class McpServer:
    def __init__(self) -> None:
        self.initialized = False

    # -- JSON-RPC ---------------------------------------------------------
    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}

        if msg_id is None:  # notification
            if method == "notifications/initialized":
                self.initialized = True
            return None

        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self._call_tool(params)
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            elif method == "logging/setLevel":
                result = {}
            else:
                raise RpcError(-32601, f"Method not found: {method}")
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except RpcError as exc:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:  # pragma: no cover
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(exc), "data": traceback.format_exc(limit=3)},
            }

    def _initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        requested = str(params.get("protocolVersion") or PROTOCOL_VERSIONS[0])
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }

    def _call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in _HANDLERS:
            raise RpcError(-32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            raise RpcError(-32602, "arguments must be an object")
        handler = _HANDLERS[name]
        allowed = set(TOOLS[next(i for i, t in enumerate(TOOLS) if t["name"] == name)]["inputSchema"]["properties"])
        unknown = [k for k in args if k not in allowed]
        if unknown:
            raise RpcError(-32602, f"Unexpected arguments for {name}: {unknown}")
        try:
            result = handler(**args)
        except impl.ToolError as exc:
            return {
                "content": [{"type": "text", "text": f"Error: {exc}"}],
                "isError": True,
            }
        except TypeError as exc:
            return {"content": [{"type": "text", "text": f"Bad arguments: {exc}"}], "isError": True}
        text = json.dumps(result, ensure_ascii=False, indent=2)
        return {"content": [{"type": "text", "text": text}], "isError": False, "structuredContent": result}


def _read_message(stream) -> Optional[str]:
    """读取一条消息，兼容换行分隔与 Content-Length 头两种帧格式。"""
    line = stream.readline()
    if not line:
        return None
    if isinstance(line, bytes):
        text = line.decode("utf-8", "replace")
    else:
        text = line
    if text.lower().startswith("content-length:"):
        try:
            length = int(text.split(":", 1)[1].strip())
        except ValueError:
            return None
        while True:
            header = stream.readline()
            if not header or (header if isinstance(header, str) else header.decode()).strip() == "":
                break
        body = stream.read(length)
        return body.decode("utf-8") if isinstance(body, bytes) else body
    return text.strip()


def serve_stdio() -> None:
    server = McpServer()
    stdin = getattr(sys.stdin, "buffer", sys.stdin)
    stdout = getattr(sys.stdout, "buffer", sys.stdout)
    while True:
        raw = _read_message(stdin)
        if raw is None:
            break
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            stdout.write((json.dumps(resp) + "\n").encode("utf-8"))
            stdout.flush()
            continue

        if isinstance(message, list):  # 兼容批量请求
            responses = [r for r in (server.handle(m) for m in message) if r]
            if responses:
                stdout.write((json.dumps(responses) + "\n").encode("utf-8"))
                stdout.flush()
            continue

        response = server.handle(message)
        if response is not None:
            stdout.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()
