"""FastAPI 后端。

关键设计：agent 循环和 UI 不缠绕。后端是独立进程——
关掉前端，`curl -N "http://127.0.0.1:8123/api/stream?task=..."` 也能看到事件流。
"""
from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent.config import env_path, project_root, settings
from agent.llm import OpenAIChat
from agent.loop import ToolRegistry, run_agent
from agent.mcp import MCPClient
from agent.tools import Workspace, kill_active_processes

HOST = "127.0.0.1"
PORT = 8123


def ui_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", project_root())) / "ui"


app = FastAPI(title="Desktop Agent")
app.mount("/static", StaticFiles(directory=str(ui_dir())), name="static")


# ---------------- 工具 / MCP ----------------
def build_registry() -> ToolRegistry:
    workspace = Workspace(settings.workspace)
    clients: list[MCPClient] = []
    cfg_path = settings.mcp_config
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            for name, s in cfg.get("mcpServers", {}).items():
                if not s.get("enabled", True):
                    continue
                cmd = [s["command"]] + list(s.get("args", []))
                try:
                    clients.append(MCPClient(cmd, cwd=project_root()))
                except Exception as e:  # 单个 server 挂了不影响整体
                    print(f"[mcp:{name}] 启动失败：{e}", file=sys.stderr)
        except Exception as e:
            print(f"[mcp] 配置解析失败：{e}", file=sys.stderr)
    return ToolRegistry(workspace, clients)


def build_chat() -> OpenAIChat:
    if not settings.has_key:
        raise RuntimeError(
            "未配置 API Key：复制 .env.example 为 .env 填入 LLM_API_KEY，"
            "或在界面右上角「设置」里填写。"
        )
    return OpenAIChat(settings.base_url, settings.api_key, settings.model)


# ---------------- REST ----------------
@app.get("/")
async def index():
    return FileResponse(str(ui_dir() / "index.html"))


@app.get("/api/health")
async def health():
    return {"status": "ok", "workspace": str(settings.workspace)}


@app.get("/api/config")
async def get_config():
    return {
        "base_url": settings.base_url,
        "model": settings.model,
        "has_key": settings.has_key,
        "workspace": str(settings.workspace),
        "max_steps": settings.max_steps,
    }


@app.post("/api/config")
async def set_config(body: dict):
    if "base_url" in body:
        settings.base_url = body["base_url"]
    if "api_key" in body:
        settings.api_key = body["api_key"]
    if "model" in body:
        settings.model = body["model"]
    if body.get("persist"):
        _persist_env()
    return {"ok": True, "has_key": settings.has_key}


def _persist_env() -> None:
    p = env_path()
    content = (
        f"LLM_BASE_URL={settings.base_url}\n"
        f"LLM_API_KEY={settings.api_key}\n"
        f"LLM_MODEL={settings.model}\n"
    )
    p.write_text(content, encoding="utf-8")


@app.get("/api/tree")
async def tree():
    return _walk(settings.workspace)


def _walk(root: Path, rel: Path = None) -> list[dict]:
    rel = rel or Path(".")
    items: list[dict] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return items
    for p in entries:
        if p.name.startswith(".") or p.name in {"__pycache__", "node_modules", ".git", "dist", "build"}:
            continue
        r = rel / p.name
        if p.is_dir():
            items.append({"name": p.name, "path": str(r).replace("\\", "/"), "type": "dir",
                          "children": _walk(p, r)})
        else:
            items.append({"name": p.name, "path": str(r).replace("\\", "/"), "type": "file",
                          "size": p.stat().st_size})
    return items


@app.get("/api/file")
async def file_content(path: str):
    ws = Workspace(settings.workspace)
    try:
        p = ws.resolve(path)
        if p.is_dir():
            return JSONResponse({"error": "是目录"}, status_code=400)
        text = p.read_text(encoding="utf-8", errors="replace")
        return {"path": path, "content": text, "lines": len(text.splitlines())}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/file")
async def save_file(body: dict):
    ws = Workspace(settings.workspace)
    try:
        r = ws.write(body["path"], body["content"])
        return {"ok": r.ok, "text": r.text}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ---------------- 事件流（SSE + WebSocket） ----------------
def _spawn_agent(task: str, cancel: threading.Event, emit) -> threading.Thread:
    def worker():
        try:
            registry = build_registry()
            chat = build_chat()
            run_agent(task, chat, registry, emit=emit, cancel=cancel)
        except Exception as e:
            emit({"type": "error", "message": f"{type(e).__name__}: {e}"})

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


@app.get("/api/stream")
async def stream(task: str):
    """SSE：`curl -N` 也能看事件流。"""
    cancel = threading.Event()
    q: queue.Queue = queue.Queue()

    def emit(ev):
        q.put(ev)

    _spawn_agent(task, cancel, emit)

    def gen():
        while True:
            ev = q.get()
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev["type"] in ("done", "error", "cancelled"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_running_loop()
    cancel = threading.Event()
    aq: asyncio.Queue = asyncio.Queue()

    def emit(ev):
        loop.call_soon_threadsafe(aq.put_nowait, ev)

    async def sender():
        # 一个连接一个常驻 sender，持续转发所有 run 的事件，直到连接关闭。
        while True:
            ev = await aq.get()
            await ws.send_json(ev)

    send_task = asyncio.create_task(sender())
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "run":
                cancel.set()          # 一次只跑一个任务，先停掉上一个
                kill_active_processes()
                cancel = threading.Event()
                _spawn_agent(data["task"], cancel, emit)
            elif data.get("type") == "interrupt":
                cancel.set()
                kill_active_processes()
    except WebSocketDisconnect:
        cancel.set()
        kill_active_processes()
    finally:
        send_task.cancel()
