"""模型层：OpenAI 兼容接口 + 一个确定性假模型（无 Key 也能跑测试/生成 trace）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class LLMReply:
    content: Optional[str] = None
    # [{"id": "call_1", "name": "read", "arguments": {...}}]
    tool_calls: list[dict] = field(default_factory=list)


class ChatBase:
    def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        raise NotImplementedError


def _extract_tool_calls(message: Any) -> list[dict]:
    calls = []
    for tc in getattr(message, "tool_calls", None) or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
    return calls


class OpenAIChat(ChatBase):
    """OpenAI 兼容接口：DeepSeek / Qwen / GLM / Moonshot / Ollama 都走这里。"""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("未安装 openai 库：pip install openai") from e
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=120.0)
        self.model = model

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        return LLMReply(content=msg.content, tool_calls=_extract_tool_calls(msg))


class ScriptedChat(ChatBase):
    """确定性假模型：按脚本逐条吐出 tool_calls。

    用途：单元测试、生成 docs/trace.md、没 Key 时演示界面。
    """

    def __init__(self, script: list[LLMReply]) -> None:
        self.script = list(script)
        self.i = 0

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        if self.i >= len(self.script):
            return LLMReply(content="（脚本已结束）任务完成。")
        reply = self.script[self.i]
        self.i += 1
        return reply
