"""纯 Python 断言测试：不需要 pytest，直接 `python test_stats.py` 跑。"""
from stats import mean

assert mean([1, 2, 3]) == 2, "mean([1,2,3]) 应该等于 2"
assert mean([10, 20, 30, 40]) == 25, "基本均值计算错误"

try:
    mean([])
except ValueError:
    print("mean([]) 正确抛出了 ValueError")
else:
    raise AssertionError("mean([]) 应该抛出 ValueError，但它返回了正常值")

print("全部通过")
