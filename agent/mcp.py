"""极简 stdio MCP 客户端（纯标准库）。

协议就是 JSON-RPC：initialize -> tools/list -> tools/call。
没有 SDK 魔法——把一个外部进程桥接成本地可用的一批工具。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from .tools import ToolResult


class MCPError(Exception):
    pass


class MCPClient:
    def __init__(self, command: list[str], cwd: Optional[Path] = None) -> None:
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, cwd=str(cwd) if cwd else None,
        )
        self._id = 0
        self._init()

    def _init(self) -> None:
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "desktop-agent", "version": "1.0.0"},
        })
        self._notify("notifications/initialized", {})

    def _request(self, method: str, params: dict) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise MCPError(f"MCP server 提前退出（{method}）：{self._read_stderr()}")
        try:
            resp = json.loads(line)
        except json.JSONDecodeError as e:
            raise MCPError(f"MCP 响应不是合法 JSON（{method}）：{line!r}") from e
        if "error" in resp:
            raise MCPError(f"MCP 错误（{method}）：{resp['error']}")
        return resp

    def _notify(self, method: str, params: dict) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read_stderr(self) -> str:
        try:
            return self.proc.stderr.read().strip()
        except Exception:
            return ""

    def list_tools(self) -> list[dict]:
        resp = self._request("tools/list", {})
        return resp.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        resp = self._request("tools/call", {"name": name, "arguments": arguments})
        result = resp.get("result", {})
        content = result.get("content", [])
        text = "\n".join(
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        )
        is_error = bool(result.get("isError", False))
        if not text:
            text = json.dumps(result, ensure_ascii=False)
        return ToolResult(text=text, ok=not is_error)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass
