"""排名归一化共享工具 — 单一实现原则"""
from __future__ import annotations


def rank_normalize_list(
    values: list[float],
    ascending: bool = True,
    handle_ties: bool = True,
) -> list[float]:
    """对一组数值做排名归一化，返回 [0, 1] 范围。

    Args:
        values: 待排名数值列表
        ascending: True=值越小排名越前(分越低); False=值越大排名越前
        handle_ties: True=相同值取平均排名; False=稳定排序
    Returns:
        归一化排名分列表 [0, 1]。n=1 → [0.0]
    """
    n = len(values)
    if n <= 1:
        return [0.0] * n

    indexed = [(v, i) for i, v in enumerate(values)]
    indexed.sort(key=lambda x: x[0], reverse=not ascending)

    scores = [0.0] * n
    divisor = max(n - 1, 1)

    if handle_ties:
        eps = 1e-10
        i = 0
        while i < n:
            j = i + 1
            while j < n and abs(indexed[j][0] - indexed[i][0]) < eps:
                j += 1
            avg_rank = sum(range(i, j)) / (j - i)
            for k in range(i, j):
                scores[indexed[k][1]] = avg_rank / divisor
            i = j
    else:
        for rank, (_, orig_idx) in enumerate(indexed):
            scores[orig_idx] = rank / divisor

    return scores
