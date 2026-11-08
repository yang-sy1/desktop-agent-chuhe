# 怎么打的包（pyinstaller）

## 1. 装打包工具

```bash
pip install pyinstaller
```

## 2. 打一个能双击的 exe

在项目根目录（Windows，`--add-data` 分隔符是 `;`）：

```bash
pyinstaller --noconfirm --clean --windowed \
  --add-data "ui;ui" \
  --add-data "mcp.json;." \
  --add-data "mcp_servers;mcp_servers" \
  --name DesktopAgent \
  main.py
```

产物在 `dist/DesktopAgent/DesktopAgent.exe`。

- `--windowed`：没有黑框控制台，双击直接出窗口。
- `--add-data`：把 `ui/`、`mcp.json`、`mcp_servers/` 一起塞进包。

## 3. 打包后的路径处理（代码里已经做好）

| 场景 | 开发时 | 打包后 |
|------|--------|--------|
| 找资源（ui/、mcp.json） | 项目根目录 | `sys._MEIPASS` |
| `.env`（可写） | 项目根目录 | `Path.home()/.desktop_agent.env` |
| `workspace`（可写） | 项目根目录 | `Path.home()/DesktopAgentWorkspace` |

对应代码：`agent/config.py` 的 `project_root()` / `env_path()` / `default_workspace()`，
以及 `server/app.py` 的 `ui_dir()`。

## 4. 发 Release

1. 在 GitHub 仓库打个 tag：`git tag v1.0.0 && git push origin v1.0.0`
2. 把 `dist/DesktopAgent/DesktopAgent.exe` 压成 zip（或直接挂 exe）作为附件。
3. Release 命名 `v1.0.0`，附件挂安装包。

## 5. 已知注意点

- **MCP 在打包版里**：`mcp_servers/weather_server.py` 是被 `python` 解释器拉的子进程，
  打包后这台机器上仍需要有 `python` 在 PATH 里。演示 MCP 建议在 `python main.py` 下跑；
  要彻底独立可改用 FastMCP 的 HTTP 方式（`mcp run --transport streamable-http`）。
- **杀毒误报**：pyinstaller 单文件（`--onefile`）更易被 Windows Defender 误报，
  建议用默认目录模式（不加 `--onefile`）。
- **换台机器成立**：README 三步要能跑，`.env` 不随包分发，Key 由用户自己填。
