"""智能体循环：ReAct 的 16 行本质 + 三道护栏 + 可观测事件流。"""
from __future__ import annotations

import json
import platform
import time
from threading import Event
from typing import Callable, Optional

from .config import settings
from .llm import ChatBase
from .mcp import MCPClient
from .tools import ToolResult, Workspace

EventSink = Callable[[dict], None]


def _system_prompt() -> str:
    return (
        f"你是运行在用户电脑上的编程智能体，工作目录是 {settings.workspace}。\n"
        f"当前系统：{platform.system()}。\n"
        "你只能通过调用工具来读写文件、执行命令，永远不要假设文件内容。\n"
        "规则：\n"
        "1. 动手改之前，先用 read 读相关文件。\n"
        "2. 改完必须用 bash 跑测试或命令验证结果。\n"
        "3. edit 的 old 必须精确（含缩进）且唯一；出现多次就加更多上下文。\n"
        "4. 读大文件用 offset/limit 分页。\n"
        "5. 不要编造 API；不确定就明确写“需要确认”。\n"
        f"列目录用 bash：Windows 用 dir，Linux/macOS 用 ls。\n"
    )


def builtin_tool_schemas() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "read",
            "description": "读取工作区内的文件，返回带行号的内容，支持分页；图片会以多模态内容块返回。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径"},
                "offset": {"type": "integer", "description": "起始行号，从 0 开始，默认 0"},
                "limit": {"type": "integer", "description": "最多读取行数，默认 2000"},
            }, "required": ["path"]},
        }},
        {"type": "function", "function": {
            "name": "write",
            "description": "写入文件（覆盖或新建），自动创建父目录，原子写。覆盖时返回前后行数。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径"},
                "content": {"type": "string", "description": "完整的文件新内容"},
            }, "required": ["path", "content"]},
        }},
        {"type": "function", "function": {
            "name": "edit",
            "description": "精确字符串替换：old 必须在文件中唯一，否则报错要求补充上下文。不做模糊匹配。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "相对工作区的文件路径"},
                "old": {"type": "string", "description": "要被替换的原文（精确、含缩进）"},
                "new": {"type": "string", "description": "替换成的内容"},
            }, "required": ["path", "old", "new"]},
        }},
        {"type": "function", "function": {
            "name": "bash",
            "description": "执行 shell 命令，返回 stdout + stderr + exit_code，非 0 也正常返回。列目录、搜索、git、跑测试都走它。",
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 60"},
            }, "required": ["command"]},
        }},
    ]


class ToolRegistry:
    """内置四工具 + 可选的 MCP 工具。执行权只在字典里（白名单）。"""

    def __init__(self, workspace: Workspace, mcp_clients: Optional[list[MCPClient]] = None) -> None:
        self.workspace = workspace
        self.clients: list[MCPClient] = list(mcp_clients or [])
        self.mcp_tools: dict[str, tuple[int, dict]] = {}
        for ci, client in enumerate(self.clients):
            for t in client.list_tools():
                self.mcp_tools[t["name"]] = (ci, t)

    def schemas(self) -> list[dict]:
        s = builtin_tool_schemas()
        for name, (_, t) in self.mcp_tools.items():
            s.append({"type": "function", "function": {
                "name": name,
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
            }})
        return s

    def source(self, name: str) -> str:
        return "mcp" if name in self.mcp_tools else "builtin"

    def execute(self, name: str, args: dict, cancel: Optional[Event] = None) -> ToolResult:
        ws = self.workspace
        if name == "read":
            args = dict(args)
            args.setdefault("limit", settings.read_limit)
            return ws.read(**args)
        if name == "write":
            return ws.write(**args)
        if name == "edit":
            return ws.edit(**args)
        if name == "bash":
            args = dict(args)
            args.setdefault("timeout", settings.bash_timeout)
            return ws.bash(**args, cancel=cancel)
        if name in self.mcp_tools:
            ci, _ = self.mcp_tools[name]
            return self.clients[ci].call_tool(name, args)
        return ToolResult(text=f"未知工具：{name}（白名单外不允许调用）", ok=False)


def _assistant_message(reply) -> dict:
    msg = {"role": "assistant", "content": reply.content or ""}
    if reply.tool_calls:
        msg["tool_calls"] = [
            {"id": c["id"], "type": "function",
             "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
            for c in reply.tool_calls
        ]
    return msg


def _tool_message(call_id: str, name: str, result: ToolResult) -> dict:
    content = result.content if result.content is not None else result.text
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def run_agent(
    task: str,
    chat: ChatBase,
    registry: ToolRegistry,
    emit: EventSink,
    cancel: Event,
    max_steps: Optional[int] = None,
) -> str:
    """跑一次 ReAct 循环，把每一步作为事件推给 emit。

    emit 事件类型：step / assistant / tool_call / tool_result / done / cancelled / error
    """
    max_steps = max_steps or settings.max_steps
    messages = [{"role": "system", "content": _system_prompt()},
                {"role": "user", "content": task}]
    t0 = time.monotonic()

    for step in range(1, max_steps + 1):
        if cancel.is_set():
            emit({"type": "cancelled", "message": "已中断"})
            return "已中断"
        emit({"type": "step", "step": step, "max_steps": max_steps})

        reply = chat.chat(messages, registry.schemas())
        if reply.content:
            emit({"type": "assistant", "text": reply.content})

        if not reply.tool_calls:
            emit({"type": "done", "answer": reply.content or "", "steps": step,
                  "elapsed": round(time.monotonic() - t0, 1)})
            return reply.content or ""

        messages.append(_assistant_message(reply))
        for call in reply.tool_calls:
            if cancel.is_set():
                emit({"type": "cancelled", "message": "已中断"})
                return "已中断"
            name, args = call["name"], call["arguments"]
            emit({"type": "tool_call", "name": name, "args": args,
                  "source": registry.source(name)})
            try:
                result = registry.execute(name, args, cancel=cancel)
            except Exception as e:  # 工具抛异常也不能让循环崩掉
                result = ToolResult(text=f"{type(e).__name__}: {e}", ok=False)
            emit({"type": "tool_result", "name": name, "text": result.text,
                  "ok": result.ok, "diff": result.diff, "source": registry.source(name)})
            messages.append(_tool_message(call["id"], name, result))

    emit({"type": "done", "answer": "达到步数上限，转人工。", "steps": max_steps,
          "elapsed": round(time.monotonic() - t0, 1), "stopped": True})
    return "达到步数上限，转人工。"


def render_trace(task: str, events: list[dict]) -> str:
    """把事件流渲染成 markdown，用于 docs/trace.md。"""
    lines = [f"# 一次完整运行记录\n\n**任务**：{task}\n"]
    for ev in events:
        t = ev["type"]
        if t == "step":
            lines.append(f"\n## Step {ev['step']}\n")
        elif t == "assistant":
            if ev.get("text"):
                lines.append(f"**模型**：{ev['text']}\n")
        elif t == "tool_call":
            src = "MCP" if ev.get("source") == "mcp" else "内置"
            lines.append(f"- 调用 `{ev['name']}`（{src}） {json.dumps(ev['args'], ensure_ascii=False)}\n")
        elif t == "tool_result":
            mark = "✅" if ev.get("ok") else "❌"
            lines.append(f"  - 结果 {mark}：\n\n```text\n{ev['text']}\n```\n")
            if ev.get("diff"):
                lines.append(f"  - diff：\n\n```diff\n{ev['diff']}\n```\n")
        elif t == "done":
            lines.append(f"\n## 最终回答\n\n{ev.get('answer')}\n")
    return "".join(lines)
