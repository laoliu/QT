"""
排名归一化共享工具 — §13.2 单一实现原则

本模块是横截面排名归一化的唯一共享实现。
所有 CB 策略和通用排名逻辑必须从此处导入，禁止在其他位置重复实现。

权威定义：
- A 股轮动策略的 rank_normalize 见 equity_rotation_base.py（不同 API，含 winsorize/NaN）
- CB / 通用列表级排名归一化：本模块 rank_normalize_list()
"""

from __future__ import annotations


def rank_normalize_list(
    values: list[float],
    ascending: bool = True,
    handle_ties: bool = True,
) -> list[float]:
    """对一组数值做排名归一化，返回 [0, 1] 范围的排名分。

    Args:
        values: 待排名数值列表
        ascending: True = 值越小排名越前（分越低）;
                   False = 值越大排名越前（分越低）
        handle_ties: True = 相同值取平均排名;
                     False = 相同值按原始顺序（稳定排序）

    Returns:
        与输入等长的归一化排名分列表，范围 [0, 1]。
        n=1 时返回 [0.0]。
    """
    n = len(values)
    if n <= 1:
        return [0.0] * n

    # 构建 (value, original_index) 对
    indexed = [(v, i) for i, v in enumerate(values)]
    indexed.sort(key=lambda x: x[0], reverse=not ascending)

    scores = [0.0] * n
    divisor = max(n - 1, 1)

    if handle_ties:
        # 平均排名法：相同值共享排名（浮点数用 epsilon 比较）
        eps = 1e-10
        i = 0
        while i < n:
            j = i + 1
            while j < n and abs(indexed[j][0] - indexed[i][0]) < eps:
                j += 1
            # i..j-1 都是相同值，取平均排名
            avg_rank = sum(range(i, j)) / (j - i)
            for k in range(i, j):
                scores[indexed[k][1]] = avg_rank / divisor
            i = j
    else:
        # 稳定排序法：相同值按原始顺序
        for rank, (_, orig_idx) in enumerate(indexed):
            scores[orig_idx] = rank / divisor

    return scores
