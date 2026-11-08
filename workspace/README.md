# 示例工作区

这是智能体的默认工作目录。`stats.py` 里有一个故意留下的 bug：

- `mean([])` 应该抛 `ValueError`，但当前返回了 `0`。

你可以直接在应用里对它说：

> mean 对空列表处理有问题，改掉，让测试通过

它应该会：读文件 → 跑测试（失败）→ edit 修复 → 再跑测试（通过）。

`test_stats.py` 是纯 Python 断言，`python test_stats.py` 即可运行，无需安装 pytest。
