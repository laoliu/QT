"""AST 因子去重 — 基于抽象语法树的结构化因子相似度检测。

核心用途：
- 检测新生成因子与已有因子库的**结构相似度**
- 拒绝与已有因子高度重复的新因子（防止因子同质化 CF-022）
- 比数值相关性去重更早期、更高效

基于：
- AlphaAgent (KDD 2025, arXiv:2502.16789) AST 正则化思路
- FactorEngine (arXiv:2603.16365) 频繁子树回避
- CogAlpha (arXiv:2511.18850) 结构多样性度量

算法：
1. 将因子表达式解析为 AST
2. 标准化 AST（常量折叠、交换律排序、变量泛化）
3. 计算 AST 结构哈希 + 树编辑距离
4. 与因子库比较，超过阈值即拒绝

与现有架构关系：
- 使用 factor_expression_parser.py 进行表达式解析
- 与 correlation.py 的数值去重互补（AST=结构层, corr=数值层）
- 供 IC Trap Guard 和 LLM 因子挖掘管道使用
"""

from __future__ import annotations

import ast
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ASTSignature:
    """因子的 AST 签名"""
    name: str
    structure_hash: str
    depth: int
    node_count: int
    operator_set: frozenset[str]
    normalized_tree_str: str


@dataclass
class DedupResult:
    """去重检测结果"""
    is_duplicate: bool
    most_similar_name: str = ""
    similarity_score: float = 0.0
    reason: str = ""


class ASTFactorDeduplicator:
    """AST 因子去重器"""

    def __init__(
        self,
        similarity_threshold: float = 0.85,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self._signatures: dict[str, ASTSignature] = {}

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def register(self, name: str, expression: str) -> ASTSignature:
        """注册已有因子到库中。"""
        sig = self.compute_signature(name, expression)
        self._signatures[name] = sig
        return sig

    def check_duplicate(
        self, name: str, expression: str,
    ) -> DedupResult:
        """检查新因子是否与已有因子重复。"""
        new_sig = self.compute_signature(name, expression)
        best_score = 0.0
        best_name = ""

        for existing_name, existing_sig in self._signatures.items():
            score = self._compute_similarity(new_sig, existing_sig)
            if score > best_score:
                best_score = score
                best_name = existing_name

        is_dup = best_score >= self.similarity_threshold
        reason = ""
        if is_dup:
            reason = (
                f"Structural similarity {best_score:.2%} >= "
                f"threshold {self.similarity_threshold:.2%} "
                f"with '{best_name}'"
            )
        return DedupResult(
            is_duplicate=is_dup,
            most_similar_name=best_name,
            similarity_score=best_score,
            reason=reason,
        )

    def batch_check(
        self,
        candidates: dict[str, str],
    ) -> dict[str, DedupResult]:
        """批量去重检查，返回 {名称: 结果}。"""
        results: dict[str, DedupResult] = {}
        for name, expr in candidates.items():
            results[name] = self.check_duplicate(name, expr)
            # 非重复的加入库（累积去重）
            if not results[name].is_duplicate:
                self.register(name, expr)
        return results

    def compute_signature(
        self, name: str, expression: str,
    ) -> ASTSignature:
        """计算因子的 AST 签名。"""
        # 将因子 DSL 的 $field 引用转换为合法 Python 标识符
        # e.g. $close → _F_close, $turnover_rate → _F_turnover_rate
        import re
        py_expr = re.sub(r'\$(\w+)', r'_F_\1', expression.strip())
        try:
            tree = ast.parse(py_expr, mode="eval")
        except SyntaxError:
            return ASTSignature(
                name=name,
                structure_hash="INVALID",
                depth=0,
                node_count=0,
                operator_set=frozenset(),
                normalized_tree_str=expression,
            )
        normalized = self._normalize_tree(tree)
        norm_str = ast.dump(normalized, annotate_fields=False)
        depth = self._tree_depth(normalized)
        count = self._node_count(normalized)
        ops = self._extract_ops(normalized)
        h = hashlib.md5(norm_str.encode()).hexdigest()[:16]

        return ASTSignature(
            name=name,
            structure_hash=h,
            depth=depth,
            node_count=count,
            operator_set=frozenset(ops),
            normalized_tree_str=norm_str,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _normalize_tree(self, tree: ast.AST) -> ast.AST:
        """标准化 AST：常量泛化 + 交换律排序 + 变量泛化。"""
        return _NormalizeVisitor().visit(tree)

    def _compute_similarity(
        self, a: ASTSignature, b: ASTSignature,
    ) -> float:
        """计算两个签名的综合相似度 [0, 1]。"""
        # 1. 精确哈希匹配
        if a.structure_hash == b.structure_hash:
            return 1.0

        # 2. 运算符集合 Jaccard 相似度
        if a.operator_set or b.operator_set:
            union = a.operator_set | b.operator_set
            inter = a.operator_set & b.operator_set
            op_sim = len(inter) / len(union) if union else 0.0
        else:
            op_sim = 1.0

        # 3. 深度/节点数相似度
        depth_sim = 1.0 - abs(a.depth - b.depth) / max(a.depth, b.depth, 1)
        count_sim = 1.0 - abs(
            a.node_count - b.node_count
        ) / max(a.node_count, b.node_count, 1)

        # 4. 字符串编辑距离相似度（轻量近似）
        str_sim = self._string_similarity(
            a.normalized_tree_str, b.normalized_tree_str
        )

        # 加权综合
        return 0.40 * str_sim + 0.30 * op_sim + 0.15 * depth_sim + 0.15 * count_sim

    def _string_similarity(self, a: str, b: str) -> float:
        """字符串相似度（基于最长公共子序列比例）。"""
        if a == b:
            return 1.0
        if not a or not b:
            return 0.0
        # 简化：使用 token 集合交集
        ta = set(a.split("("))
        tb = set(b.split("("))
        union = ta | tb
        inter = ta & tb
        return len(inter) / len(union) if union else 0.0

    def _tree_depth(self, node: ast.AST) -> int:
        """计算 AST 深度。"""
        if not list(ast.iter_child_nodes(node)):
            return 1
        return 1 + max(
            self._tree_depth(c) for c in ast.iter_child_nodes(node)
        )

    def _node_count(self, node: ast.AST) -> int:
        """计算 AST 节点数。"""
        return sum(1 for _ in ast.walk(node))

    def _extract_ops(self, tree: ast.AST) -> list[str]:
        """提取运算符名称。"""
        ops: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                ops.append(node.func.id)
        return ops


class _NormalizeVisitor(ast.NodeTransformer):
    """AST 标准化访问器。"""

    _var_counter: int = 0

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        """常量泛化：所有数字 → 占位符 `_C`。"""
        if isinstance(node.value, (int, float)):
            return ast.Name(id="_C", ctx=ast.Load())
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        """变量标准化：保留运算符名称，泛化数据变量。"""
        from aipha.factors.factor_expression_parser import ALLOWED_OPERATORS
        if node.id in ALLOWED_OPERATORS or node.id.startswith("_C"):
            return node
        # 保持 _F_ 前缀的数据字段名
        if node.id.startswith("_F_"):
            return node
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        """交换律排序：对可交换运算符排序子节点。"""
        self.generic_visit(node)
        if isinstance(node.op, (ast.Add, ast.Mult)):
            left_s = ast.dump(node.left, annotate_fields=False)
            right_s = ast.dump(node.right, annotate_fields=False)
            if left_s > right_s:
                node.left, node.right = node.right, node.left
        return node
