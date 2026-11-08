"""四个工具的语义测试（可 pytest 跑，也可 `python tests/test_tools.py` 直接跑）。"""
from __future__ import annotations

import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools import ToolError, Workspace  # noqa: E402


@contextmanager
def ws():
    with tempfile.TemporaryDirectory() as d:
        yield Workspace(Path(d))


def test_read_line_numbers_and_pagination():
    with ws() as w:
        w.write("a.py", "\n".join(f"line{i}" for i in range(10)))
        r = w.read("a.py", offset=0, limit=3)
        assert r.ok
        assert "line0" in r.text and "1\tline0" in r.text
        assert "还有 7 行未显示" in r.text  # 分页提示


def test_read_path_escape_rejected():
    with ws() as w:
        try:
            w.read("../secret.txt")
            assert False, "应拒绝越界路径"
        except ToolError:
            pass


def test_read_missing_file():
    with ws() as w:
        r = w.read("nope.py")
        assert not r.ok and "不存在" in r.text


def test_write_creates_parent_dirs():
    with ws() as w:
        r = w.write("deep/nested/x.py", "print(1)\n")
        assert r.ok and "已新建" in r.text
        assert (w.root / "deep" / "nested" / "x.py").exists()


def test_write_overwrite_reports_line_counts():
    with ws() as w:
        w.write("x.py", "a\nb\nc\n")
        r = w.write("x.py", "a\nb\n")
        assert r.ok and "原 3 行" in r.text and "现 2 行" in r.text


def test_edit_unique_replacement():
    with ws() as w:
        w.write("x.py", "def f():\n    return 0\n")
        r = w.edit("x.py", "    return 0", "    raise ValueError()")
        assert r.ok
        assert "raise ValueError" in (w.root / "x.py").read_text()
        assert r.diff and "return 0" in r.diff and "raise ValueError" in r.diff


def test_edit_non_unique_rejected():
    with ws() as w:
        w.write("x.py", "return 0\nreturn 0\nreturn 0\n")
        r = w.edit("x.py", "return 0", "return 1")
        assert not r.ok and "出现 3 次" in r.text and "补充" in r.text


def test_edit_not_found():
    with ws() as w:
        w.write("x.py", "hello\n")
        r = w.edit("x.py", "goodbye", "x")
        assert not r.ok and "找不到" in r.text


def test_bash_ok_and_nonzero():
    with ws() as w:
        ok = w.bash("echo hi")
        assert ok.ok and "hi" in ok.text and "exit_code=0" in ok.text
        bad = w.bash("python -c \"import sys; sys.exit(3)\"")
        assert not bad.ok and "exit_code=3" in bad.text


def test_bash_timeout():
    with ws() as w:
        import platform
        cmd = "python -c \"import time; time.sleep(30)\""
        r = w.bash(cmd, timeout=1)
        assert not r.ok and "超时" in r.text


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
