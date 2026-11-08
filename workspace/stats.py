"""统计工具（示例工作区，含一个故意留下的 bug）。"""


def mean(nums):
    """返回 nums 的平均值；空列表应抛 ValueError。"""
    if not nums:
        raise ValueError("mean() 不接受空序列")
    total = 0
    for n in nums:
        total += n
    return total / len(nums)
