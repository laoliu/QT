"""§13.2 rank_utils.py 单元测试

验证排名归一化共享实现的正确性：
- ascending / descending
- ties 处理
- 边界条件（空列表、单元素）
"""

from __future__ import annotations

import pytest

from aipha.utils.rank_utils import rank_normalize_list


class TestRankNormalizeList:
    """rank_normalize_list 完整测试"""

    def test_ascending_basic(self):
        """升序：值越小排名越前（分越低）"""
        result = rank_normalize_list([30, 10, 20], ascending=True)
        # 排序: 10(idx1)=rank0, 20(idx2)=rank1, 30(idx0)=rank2
        # 归一化: [2/2, 0/2, 1/2] = [1.0, 0.0, 0.5]
        assert result == [1.0, 0.0, 0.5]

    def test_descending_basic(self):
        """降序：值越大排名越前（分越低）"""
        result = rank_normalize_list([30, 10, 20], ascending=False)
        # 排序: 30(idx0)=rank0, 20(idx2)=rank1, 10(idx1)=rank2
        # 归一化: [0/2, 2/2, 1/2] = [0.0, 1.0, 0.5]
        assert result == [0.0, 1.0, 0.5]

    def test_ties_averaged(self):
        """相同值取平均排名"""
        result = rank_normalize_list([10, 20, 20, 30], ascending=True)
        # 排序: 10=rank0, 20=rank1.5(avg 1,2), 30=rank3
        # 归一化: [0/3, 1.5/3, 1.5/3, 3/3]
        assert result[0] == 0.0
        assert abs(result[1] - 0.5) < 1e-10
        assert abs(result[2] - 0.5) < 1e-10
        assert result[3] == 1.0

    def test_no_ties_mode(self):
        """handle_ties=False 时相同值保持素顺序"""
        result = rank_normalize_list(
            [10, 20, 20, 30], ascending=True, handle_ties=False
        )
        assert result[0] == 0.0
        # 两个 20 按原始顺序分配 rank 1 和 rank 2
        assert result[1] != result[2]
        assert result[3] == 1.0

    def test_empty_list(self):
        """空列表返回空"""
        assert rank_normalize_list([]) == []

    def test_single_element(self):
        """单元素返回 [0.0]"""
        assert rank_normalize_list([42]) == [0.0]

    def test_two_elements(self):
        """两元素"""
        result = rank_normalize_list([5, 3], ascending=True)
        assert result == [1.0, 0.0]

    def test_all_same(self):
        """全相同值 → 全相同排名"""
        result = rank_normalize_list([7, 7, 7], ascending=True)
        # avg rank = (0+1+2)/3 = 1, normalized = 1/2 = 0.5
        assert all(abs(v - 0.5) < 1e-10 for v in result)

    def test_range_0_to_1(self):
        """输出范围始终在 [0, 1]"""
        import random
        random.seed(42)
        vals = [random.random() * 1000 for _ in range(100)]
        result = rank_normalize_list(vals)
        assert all(0.0 <= v <= 1.0 for v in result)

    def test_preserves_length(self):
        """输出长度与输入一致"""
        vals = list(range(50))
        result = rank_normalize_list(vals)
        assert len(result) == 50
