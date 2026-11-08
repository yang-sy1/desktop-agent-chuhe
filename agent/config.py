"""运行时配置。

配置来源优先级：环境变量 / .env > 默认值。
UI 里的“设置”面板只改内存值；勾选“持久化”才写回 .env。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # 可选依赖：没装 dotenv 也能跑，只是不读 .env
    def load_dotenv(*args, **kwargs):
        return False


def project_root() -> Path:
    # 开发时是项目根目录；pyinstaller 打包后资源解压在 sys._MEIPASS
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def env_path() -> Path:
    # 打包后 Program Files 通常不可写，.env 改放到用户主目录
    if getattr(sys, "frozen", False):
        return Path.home() / ".desktop_agent.env"
    return project_root() / ".env"


def default_workspace() -> Path:
    if getattr(sys, "frozen", False):
        return Path.home() / "DesktopAgentWorkspace"
    return project_root() / "workspace"


class Settings:
    """单例配置。字段可在运行时被 UI 修改。"""

    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        load_dotenv(env_path(), override=False)
        self.base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
        self.api_key: str = os.getenv("LLM_API_KEY", "")
        self.model: str = os.getenv("LLM_MODEL", "deepseek-chat")
        self.workspace: Path = Path(os.getenv("WORKSPACE_DIR", str(default_workspace())))
        self.max_steps: int = int(os.getenv("AGENT_MAX_STEPS", "8"))
        self.bash_timeout: int = int(os.getenv("BASH_TIMEOUT", "60"))
        self.read_limit: int = int(os.getenv("READ_LIMIT", "2000"))
        self.mcp_config: Path = Path(os.getenv("MCP_CONFIG", str(project_root() / "mcp.json")))

    @property
    def has_key(self) -> bool:
        return bool(self.api_key.strip())


settings = Settings()
