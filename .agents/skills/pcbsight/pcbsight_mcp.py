"""
pcbsight_mcp.py - Model Context Protocol (MCP) Server for PCBsight.
Provides first-class executable tools for AI coding assistants (ChatGPT, OpenAI Codex, Claude Code, Antigravity).
Supports both standard stdio transport and HTTP transport without external dependencies.
"""

from __future__ import annotations
import sys
import os
import json
import traceback
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ensure scripts directory and vendor are in sys.path
_base_dir = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.join(_base_dir, "scripts")
_vendor_dir = os.path.join(_scripts_dir, "vendor")

for p in [_scripts_dir, _vendor_dir, _base_dir]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

try:
    from pcbdoc_parser import PCBDocParser
    from pcb_analyzer import PCBAnalyzer
    from pcb_reporter import PcbReporter
except ImportError:
    _skill_scripts = os.path.join(_base_dir, "skills", "pcbsight", "scripts")
    if os.path.exists(_skill_scripts) and _skill_scripts not in sys.path:
        sys.path.insert(0, _skill_scripts)
    from pcbdoc_parser import PCBDocParser
    from pcb_analyzer import PCBAnalyzer
    from pcb_reporter import PcbReporter


TOOLS = [
    {
        "name": "pcbsight_inspect",
        "description": "Inspect an Altium Designer .PcbDoc file. Returns comprehensive board metrics: outline dimensions (width, height, area), physical layer stackup, copper layers, total component counts, routed track length, design rule counts, and recorded DRC violations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative path to the Altium .PcbDoc file."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "pcbsight_rules",
        "description": "Audit design rules, clearance constraints, and recorded DRC violations in an Altium .PcbDoc file. Pinpoints colliding primitives (e.g. Pad <-> Pad), coordinates, and clearance gap measurements.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative path to the Altium .PcbDoc file."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "pcbsight_layers",
        "description": "Extract full physical layer stackup, dielectric materials, copper thickness, dielectric thickness, and technical layers (solder mask, overlay/silkscreen, paste, mechanical) from an Altium .PcbDoc file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative path to the Altium .PcbDoc file."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "pcbsight_bom",
        "description": "Extract placed components and Bill of Materials (BOM) from an Altium .PcbDoc file. Includes Designator, Footprint pattern, Layer (Top/Bottom), MPN, Value, Description, and SMD vs THT breakdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative path to the Altium .PcbDoc file."
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "pcbsight_report",
        "description": "Generate a comprehensive GitHub-flavored Markdown report, full JSON dump, CSV tables (BOM, drills, nets), and SVG layer renders from an Altium .PcbDoc file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or workspace-relative path to the Altium .PcbDoc file."
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory to save generated artifacts (default: './output')."
                },
                "export_all": {
                    "type": "boolean",
                    "description": "Whether to export CSVs and SVG renders alongside Markdown and JSON reports (default: true)."
                }
            },
            "required": ["file_path"]
        }
    }
]


def _resolve_file(file_path: str) -> str:
    """Resolve file path relative to current working directory or absolute."""
    if os.path.isabs(file_path):
        resolved = file_path
    else:
        resolved = os.path.abspath(file_path)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"PCB file does not exist: {file_path} (resolved to {resolved})")
    return resolved


def handle_tool_call(name: str, args: dict) -> str:
    file_path = args.get("file_path", "")
    if not file_path:
        raise ValueError("Missing required argument: 'file_path'")
    resolved_path = _resolve_file(file_path)

    parser = PCBDocParser(resolved_path)
    result = parser.parse()
    analyzer = PCBAnalyzer(result)

    if name == "pcbsight_inspect":
        summary = analyzer.get_summary()
        return json.dumps(summary, indent=2, ensure_ascii=False)

    elif name == "pcbsight_rules":
        audit = analyzer.get_rule_audit()
        return json.dumps(audit, indent=2, ensure_ascii=False)

    elif name == "pcbsight_layers":
        stackup = analyzer.get_stackup_table()
        return json.dumps(stackup, indent=2, ensure_ascii=False)

    elif name == "pcbsight_bom":
        bom = analyzer.get_bom_summary()
        return json.dumps(bom, indent=2, ensure_ascii=False)

    elif name == "pcbsight_report":
        output_dir = args.get("output_dir", "./output")
        export_all = args.get("export_all", True)
        os.makedirs(output_dir, exist_ok=True)

        reporter = PcbReporter(result)
        stem = os.path.splitext(result.filename)[0]
        md_file = os.path.join(output_dir, f"{stem}_report.md")
        json_file = os.path.join(output_dir, f"{stem}_data.json")

        with open(md_file, "w", encoding="utf-8") as f:
            f.write(reporter.to_markdown())
        reporter.to_json(json_file)

        files = [md_file, json_file]
        if export_all:
            bom_file = os.path.join(output_dir, "bom.csv")
            drills_file = os.path.join(output_dir, "drills.csv")
            nets_file = os.path.join(output_dir, "nets.csv")
            svg_dir = os.path.join(output_dir, "svgs")

            reporter.to_csv_bom(bom_file)
            reporter.to_csv_drills(drills_file)
            reporter.to_csv_nets(nets_file)
            svg_files = reporter.export_svgs(svg_dir)
            files.extend([bom_file, drills_file, nets_file] + svg_files)

        output_msg = f"PCBsight Analysis Completed Successfully.\nGenerated Artifacts ({len(files)} files):\n"
        for p in files:
            output_msg += f"- {os.path.abspath(p)}\n"
        output_msg += "\nExecutive Summary:\n"
        output_msg += json.dumps(analyzer.get_summary(), indent=2, ensure_ascii=False)
        return output_msg

    else:
        raise ValueError(f"Unknown tool name: {name}")


def process_mcp_message(req: dict) -> dict | None:
    """Process a standard JSON-RPC 2.0 MCP request and return the response dictionary."""
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "pcbsight-mcp",
                    "version": "1.0.0"
                }
            }
        }

    elif method == "notifications/initialized":
        return None

    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {}
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS
            }
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        try:
            result_text = handle_tool_call(tool_name, tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": result_text
                        }
                    ]
                }
            }
        except Exception as e:
            err_msg = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": err_msg
                        }
                    ]
                }
            }

    else:
        if req_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
        return None


def run_stdio_server():
    """Run JSON-RPC 2.0 loop reading from stdin and writing to stdout."""
    sys.stderr.write("PCBsight MCP stdio server starting...\n")
    sys.stderr.flush()

    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue

        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
                while True:
                    hdr = sys.stdin.readline().strip()
                    if not hdr:
                        break
                payload = sys.stdin.read(length)
                req = json.loads(payload)
            except Exception as e:
                sys.stderr.write(f"Error parsing Content-Length frame: {e}\n")
                continue
        else:
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue

        resp = process_mcp_message(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


class McpHttpHandler(BaseHTTPRequestHandler):
    """HTTP handler supporting JSON-RPC POST requests for remote cloud MCP deployments."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        status_info = {
            "status": "healthy",
            "service": "pcbsight-mcp",
            "version": "1.0.0",
            "tools": [t["name"] for t in TOOLS]
        }
        self.wfile.write(json.dumps(status_info, indent=2).encode("utf-8"))

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len)
        try:
            req = json.loads(post_data.decode("utf-8"))
            resp = process_mcp_message(req)
            if resp is None:
                resp = {"jsonrpc": "2.0", "result": "acknowledged"}
            status_code = 200
        except Exception as e:
            status_code = 400
            resp = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"}
            }

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))


def run_http_server(host: str = "0.0.0.0", port: int = 8000):
    """Run lightweight HTTP server for remote cloud MCP hosting."""
    server = HTTPServer((host, port), McpHttpHandler)
    print(f"PCBsight MCP HTTP Server running on http://{host}:{port}/mcp")
    print(f"Exposing {len(TOOLS)} tools: {[t['name'] for t in TOOLS]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        print("\nServer stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PCBsight MCP Server")
    parser.add_argument("--http", action="store_true", help="Run as remote HTTP server instead of stdio")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP server port (default: 8000)")
    args = parser.parse_args()

    if args.http:
        run_http_server(args.host, args.port)
    else:
        run_stdio_server()
