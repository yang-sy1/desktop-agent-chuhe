"""桌面入口：双击出一个原生窗口（不是浏览器）。

流程：后台线程跑 FastAPI -> pywebview 开原生窗口加载它。
打包入口也是这个文件（pyinstaller main.py）。
"""
from __future__ import annotations

import threading

import uvicorn


def _start_backend() -> None:
    from server.app import app, HOST, PORT
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()


def main() -> None:
    _start_backend()
    import webview  # 延迟导入，便于纯后端运行时不需要装 pywebview

    webview.create_window(
        "Desktop Agent — 你自己的 Cursor",
        f"http://127.0.0.1:8123/",
        width=1280,
        height=820,
        min_size=(960, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
