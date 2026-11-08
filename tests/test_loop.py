"""循环与护栏测试（用 ScriptedChat 模拟模型，不依赖 API Key）。"""
from __future__ import annotations

import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.llm import LLMReply, ScriptedChat  # noqa: E402
from agent.loop import ToolRegistry, run_agent  # noqa: E402
from agent.tools import Workspace  # noqa: E402


@contextmanager
def registry():
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(Path(d))
        yield ws, ToolRegistry(ws, [])


def test_loop_reaches_final_answer():
    with registry() as (ws, reg):
        script = [
            LLMReply(content="先看看文件", tool_calls=[
                {"id": "c1", "name": "bash", "arguments": {"command": "echo ok"}},
            ]),
            LLMReply(content="做完了"),
        ]
        events = []
        ans = run_agent("测试任务", ScriptedChat(script), reg, emit=events.append, cancel=Event())
        assert ans == "做完了"
        assert events[-1]["type"] == "done"
        assert any(e["type"] == "tool_call" for e in events)


def test_loop_max_steps_guardrail():
    with registry() as (ws, reg):
        script = [LLMReply(tool_calls=[
            {"id": f"c{i}", "name": "bash", "arguments": {"command": "echo loop"}},
        ]) for i in range(50)]  # 永远不结束
        events = []
        ans = run_agent("死循环", ScriptedChat(script), reg, emit=events.append,
                        cancel=Event(), max_steps=3)
        assert "转人工" in ans  # 护栏：步数上限
        assert events[-1]["type"] == "done" and events[-1].get("stopped")


def test_loop_cancel():
    with registry() as (ws, reg):
        script = [LLMReply(tool_calls=[
            {"id": "c1", "name": "bash", "arguments": {"command": "echo x"}},
        ])]
        cancel = Event()
        cancel.set()  # 一开始就要求中断
        events = []
        ans = run_agent("任务", ScriptedChat(script), reg, emit=events.append, cancel=cancel)
        assert ans == "已中断"
        assert events[-1]["type"] == "cancelled"


def test_loop_unknown_tool_whitelist():
    with registry() as (ws, reg):
        script = [LLMReply(tool_calls=[
            {"id": "c1", "name": "rm_rf_everything", "arguments": {}},
        ]), LLMReply(content="结束")]
        events = []
        run_agent("越权", ScriptedChat(script), reg, emit=events.append, cancel=Event())
        res = [e for e in events if e["type"] == "tool_result"][0]
        assert "白名单" in res["text"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
