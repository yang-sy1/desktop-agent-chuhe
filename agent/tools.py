"""四个工具：read / write / edit / bash。

每个工具都是“普通函数 + 一张说明书”，说明书写在 loop.py 的
builtin_tool_schemas() 里。这里只关心：语义对不对、边界守不守得住。
"""
from __future__ import annotations

import base64
import difflib
import mimetypes
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Optional

# 正在运行的 bash 子进程，供“中断”统一清理
_ACTIVE_PROCS: set[subprocess.Popen] = set()


def kill_active_processes() -> None:
    """中断时调用：杀掉所有还在跑的 bash 子进程。"""
    for p in list(_ACTIVE_PROCS):
        try:
            p.kill()
        except Exception:
            pass


@dataclass
class ToolResult:
    """工具返回给模型（和界面）的统一结果。"""
    text: str
    ok: bool = True
    content: Optional[list[dict]] = None  # 多模态内容块（图片），优先级高于 text
    diff: Optional[str] = None            # edit/write 的统一 diff，给界面展示


class ToolError(Exception):
    pass


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or ""


def _unified_diff(before: str, after: str, path: str) -> str:
    a = before.splitlines(keepends=True)
    b = after.splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=f"a/{path}", tofile=f"b/{path}"))


class Workspace:
    """四个工具的宿主。所有文件操作都被限制在工作区目录内（路径白名单）。"""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str) -> Path:
        p = (self.root / path).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise ToolError(f"路径越界：{path}（只允许访问工作区 {self.root} 内的文件）")
        return p

    # ---------------- read ----------------
    def read(self, path: str, offset: int = 0, limit: Optional[int] = None) -> ToolResult:
        limit = limit if limit is not None else 2000
        p = self.resolve(path)
        if not p.exists():
            return ToolResult(text=f"文件不存在：{path}", ok=False)
        if p.is_dir():
            return ToolResult(
                text=f"{path} 是目录。列目录请用 bash（Windows 用 dir，其它用 ls），读文件请指定具体文件名。",
                ok=False,
            )

        mime = _guess_mime(p)
        if mime.startswith("image/"):
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            note = f"[图片] {path}（{mime}，约 {len(b64) // 1024} KB）已作为多模态内容块返回。"
            return ToolResult(
                text=note,
                ok=True,
                content=[
                    {"type": "text", "text": note},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            )

        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total = len(lines)
        if offset < 0:
            offset = 0
        if offset >= total:
            return ToolResult(text=f"offset={offset} 超出范围：{path} 只有 {total} 行。", ok=False)
        chunk = lines[offset : offset + limit]
        numbered = "\n".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(chunk))
        shown_end = offset + len(chunk)
        remaining = total - shown_end
        out = f"{path} 共 {total} 行，显示第 {offset + 1}–{shown_end} 行：\n{numbered}"
        if remaining > 0:
            out += f"\n…… 后面还有 {remaining} 行未显示（用 offset={shown_end} 继续读）"
        return ToolResult(text=out, ok=True)

    # ---------------- write ----------------
    def write(self, path: str, content: str) -> ToolResult:
        p = self.resolve(path)
        existed = p.exists()
        before = p.read_text(encoding="utf-8") if existed else ""
        old_lines = len(before.splitlines()) if existed else 0
        new_lines = len(content.splitlines())
        self._atomic_write(p, content)
        if existed:
            return ToolResult(
                text=f"已覆盖 {path}：原 {old_lines} 行 → 现 {new_lines} 行。",
                ok=True,
                diff=_unified_diff(before, content, path),
            )
        return ToolResult(text=f"已新建 {path}：{new_lines} 行。", ok=True)

    # ---------------- edit ----------------
    def edit(self, path: str, old: str, new: str) -> ToolResult:
        p = self.resolve(path)
        if not p.exists():
            return ToolResult(text=f"文件不存在：{path}", ok=False)
        if old == "":
            return ToolResult(text="edit 的 old 不能为空字符串。", ok=False)
        content = p.read_text(encoding="utf-8")
        count = content.count(old)
        if count == 0:
            return ToolResult(
                text=f"在 {path} 中找不到：\n{old}\n请先 read 确认精确内容（含缩进）。",
                ok=False,
            )
        if count > 1:
            locs = [i + 1 for i, line in enumerate(content.splitlines()) if old in line]
            return ToolResult(
                text=f"“{old}” 在 {path} 中出现 {count} 次（第 {locs} 行），不唯一。请补充更多上下文使其唯一后再改。",
                ok=False,
            )
        new_content = content.replace(old, new, 1)
        self._atomic_write(p, new_content)
        return ToolResult(
            text=f"已替换 {path}：原 {len(content.splitlines())} 行 → 现 {len(new_content.splitlines())} 行。",
            ok=True,
            diff=_unified_diff(content, new_content, path),
        )

    # ---------------- bash ----------------
    def bash(self, command: str, timeout: int = 60, cancel: Optional[Event] = None) -> ToolResult:
        proc = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(self.root),
        )
        _ACTIVE_PROCS.add(proc)
        reason = None
        deadline = time.monotonic() + timeout
        try:
            while proc.poll() is None:
                if cancel is not None and cancel.is_set():
                    reason = "interrupted"
                    proc.kill()
                    break
                if time.monotonic() > deadline:
                    reason = "timeout"
                    proc.kill()
                    break
                time.sleep(0.05)
            out, err = proc.communicate()
        finally:
            _ACTIVE_PROCS.discard(proc)

        if reason == "timeout":
            return ToolResult(text=f"命令超时（>{timeout}s）已终止：{command}", ok=False)
        if reason == "interrupted":
            return ToolResult(text=f"命令已中断：{command}", ok=False)

        code = proc.returncode
        parts = [f"$ {command}", f"exit_code={code}"]
        if (out or "").strip():
            parts.append("stdout:\n" + out.rstrip())
        if (err or "").strip():
            parts.append("stderr:\n" + err.rstrip())
        return ToolResult(text="\n".join(parts), ok=(code == 0))

    # ---------------- 内部 ----------------
    def _atomic_write(self, p: Path, content: str) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, p)  # 同一文件系统内原子替换，避免写一半崩溃
