"""桌面智能体核心包：循环、工具、模型、MCP。"""
from .config import settings
from .loop import run_agent

__all__ = ["run_agent", "settings"]
