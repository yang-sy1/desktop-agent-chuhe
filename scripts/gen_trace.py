"""生成 docs/trace.md：用脚本化模型复刻课程 demo 轨迹。

真实跑工具（真读文件、真跑测试、真 edit），只是“模型决策”是脚本写死的——
这样无 Key 也能产出一份含“失败→恢复”的真实运行记录。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.llm import LLMReply, ScriptedChat  # noqa: E402
from agent.loop import ToolRegistry, render_trace, run_agent  # noqa: E402
from agent.tools import Workspace  # noqa: E402

DIR_CMD = "dir /b" if sys.platform == "win32" else "ls -1"
TEST_CMD = "python test_stats.py"


def main() -> None:
    script = [
        LLMReply(content="我先看看工作区里有什么文件。", tool_calls=[
            {"id": "call_1", "name": "bash", "arguments": {"command": DIR_CMD}},
        ]),
        LLMReply(content="先读 stats.py 看实现。", tool_calls=[
            {"id": "call_2", "name": "read", "arguments": {"path": "stats.py"}},
        ]),
        LLMReply(content="再读测试，确认期望行为。", tool_calls=[
            {"id": "call_3", "name": "read", "arguments": {"path": "test_stats.py"}},
        ]),
        LLMReply(content="跑一下测试看现状。", tool_calls=[
            {"id": "call_4", "name": "bash", "arguments": {"command": TEST_CMD}},
        ]),
        LLMReply(content="测试失败：mean([]) 应该抛 ValueError。定位到 stats.py 第 6 行，修复它。", tool_calls=[
            {"id": "call_5", "name": "edit", "arguments": {
                "path": "stats.py",
                "old": "        return 0",
                "new": "        raise ValueError(\"mean() of empty list is undefined\")",
            }},
        ]),
        LLMReply(content="再跑一次测试确认修复。", tool_calls=[
            {"id": "call_6", "name": "bash", "arguments": {"command": TEST_CMD}},
        ]),
        LLMReply(content="修复完成：把 stats.py 里的 `return 0` 改成了抛 ValueError，`python test_stats.py` 已全部通过。"),
    ]
    task = "mean 对空列表处理有问题，改掉，让测试通过。"

    # 用临时副本跑，不污染真实 workspace
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "workspace"
        shutil.copytree(ROOT / "workspace", tmp)
        registry = ToolRegistry(Workspace(tmp), [])
        events: list[dict] = []
        run_agent(task, ScriptedChat(script), registry, emit=events.append, cancel=Event(), max_steps=8)
        md = render_trace(task, events)

    (ROOT / "docs" / "trace.md").write_text(md, encoding="utf-8")
    print(f"已生成 {ROOT / 'docs' / 'trace.md'}")


if __name__ == "__main__":
    main()
