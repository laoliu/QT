"""因子表达式解析器 — 将文本公式转换为 operators.py 可执行调用链。

核心用途：
- 解析 RD-Agent / AlphaAgent / LLM 输出的因子表达式字符串
- 将表达式转译为 Aipha-Q operators.py 的运算符调用
- 支持安全沙箱执行，防止代码注入

表达式语法（兼容 WorldQuant Alpha101 / Qlib 算子格式）::

    ts_mean($close, 20)               # 时序算子
    cs_rank(ts_corr($close, $volume, 10))  # 嵌套
    safe_div($close - ts_delay($close, 5), ts_delay($close, 5))  # 动量
    -1 * ts_rank(ts_delta($volume, 3))  # 取负

与现有架构关系：
- 运算符映射到 factors/operators.py (ts_*, cs_*, safe_div 等)
- 产出因子可注册到 FactorRegistry
- 供 IC Trap Guard 管道和 LLM 因子挖掘主流程使用

参考：
- FactorEngine (arXiv:2603.16365) 程序级因子表示
- RD-Agent (NeurIPS 2025) Qlib 因子输出格式
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 允许的运算符白名单（安全沙箱）
ALLOWED_OPERATORS: set[str] = {
    # 时序
    "ts_delay", "ts_delta", "ts_sum", "ts_mean", "ts_std", "ts_var",
    "ts_min", "ts_max", "ts_argmax", "ts_argmin", "ts_rank",
    "ts_quantile", "ts_corr", "ts_cov", "ts_slope", "ts_rsquare",
    "ts_resi", "ts_product", "ts_decay_linear", "ts_log", "ts_abs",
    "ts_sign",
    # 截面
    "cs_rank", "cs_scale", "cs_mean", "cs_std", "cs_zscore",
    # 数学辅助
    "safe_div", "pow1", "pow2", "quesval", "quesval2",
    "ts_greater", "ts_less",
}

# 字段名映射：$field → DataFrame 列名
FIELD_PATTERN = re.compile(r"\$(\w+)")

# Qlib 兼容别名映射
QLIB_ALIASES: dict[str, str] = {
    "Ref": "ts_delay",
    "Mean": "ts_mean",
    "Std": "ts_std",
    "Sum": "ts_sum",
    "Max": "ts_max",
    "Min": "ts_min",
    "Corr": "ts_corr",
    "Cov": "ts_cov",
    "Delta": "ts_delta",
    "Rank": "cs_rank",
    "CSRank": "cs_rank",
}


@dataclass
class ParsedFactor:
    """解析后的因子描述"""
    name: str
    raw_expression: str
    normalized_expression: str
    required_fields: list[str] = field(default_factory=list)
    used_operators: list[str] = field(default_factory=list)
    is_valid: bool = True
    error_message: str = ""
    ast_tree: Optional[ast.AST] = field(default=None, repr=False)


class FactorExpressionParser:
    """因子表达式解析器 — 文本公式 → 可执行代码"""

    def __init__(
        self,
        extra_aliases: Optional[dict[str, str]] = None,
    ) -> None:
        self.aliases = dict(QLIB_ALIASES)
        if extra_aliases:
            self.aliases.update(extra_aliases)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def parse(self, expression: str, name: str = "") -> ParsedFactor:
        """解析因子表达式，返回 ParsedFactor 描述。

        Args:
            expression: 因子表达式字符串
            name: 因子名称（可选）
        """
        norm = self._normalize(expression)
        fields = self._extract_fields(norm)
        try:
            tree = ast.parse(norm, mode="eval")
            ops = self._extract_operators(tree)
            self._validate_operators(ops)
            return ParsedFactor(
                name=name or self._auto_name(norm),
                raw_expression=expression,
                normalized_expression=norm,
                required_fields=fields,
                used_operators=ops,
                is_valid=True,
                ast_tree=tree,
            )
        except Exception as e:
            return ParsedFactor(
                name=name or "invalid",
                raw_expression=expression,
                normalized_expression=norm,
                required_fields=fields,
                is_valid=False,
                error_message=str(e),
            )

    def execute(
        self,
        parsed: ParsedFactor,
        data: pd.DataFrame,
        group_col: str = "symbol",
        datetime_col: str = "datetime",
    ) -> pd.Series:
        """在数据上执行已解析的因子表达式。

        Args:
            parsed: parse() 返回的 ParsedFactor
            data: 面板数据 (datetime, symbol, OHLCV fields)
            group_col: 分组列名
            datetime_col: 日期列名

        Returns:
            因子值 Series（与 data 同索引）
        """
        if not parsed.is_valid:
            raise ValueError(f"Invalid expression: {parsed.error_message}")

        namespace = self._build_namespace(data, group_col, datetime_col)
        try:
            result = eval(  # noqa: S307 — sandboxed via whitelist
                compile(parsed.ast_tree, "<factor>", "eval"),  # type: ignore[arg-type]
                {"__builtins__": {}},
                namespace,
            )
            if isinstance(result, pd.Series):
                return result.rename(parsed.name)
            return pd.Series(result, index=data.index, name=parsed.name)
        except Exception as e:
            raise RuntimeError(
                f"Factor execution failed for '{parsed.name}': {e}"
            ) from e

    def batch_parse(
        self,
        expressions: dict[str, str],
    ) -> list[ParsedFactor]:
        """批量解析多个因子表达式。

        Args:
            expressions: {因子名: 表达式} 字典
        """
        return [self.parse(expr, name) for name, expr in expressions.items()]

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _normalize(self, expr: str) -> str:
        """标准化表达式：别名替换 + $field → _F_field。"""
        result = expr.strip()
        # 替换 Qlib 别名
        for alias, target in sorted(
            self.aliases.items(), key=lambda x: -len(x[0])
        ):
            result = re.sub(rf"\b{re.escape(alias)}\b", target, result)
        # $field → _F_field（Python 合法标识符）
        result = FIELD_PATTERN.sub(r"_F_\1", result)
        return result

    def _extract_fields(self, normalized: str) -> list[str]:
        """提取表达式中引用的数据字段。"""
        return sorted(set(re.findall(r"_F_(\w+)", normalized)))

    def _extract_operators(self, tree: ast.AST) -> list[str]:
        """从 AST 中提取调用的运算符名称。"""
        ops: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                ops.append(node.func.id)
        return sorted(set(ops))

    def _validate_operators(self, ops: list[str]) -> None:
        """验证所有运算符在白名单内。"""
        disallowed = set(ops) - ALLOWED_OPERATORS
        if disallowed:
            raise ValueError(
                f"Disallowed operators: {disallowed}. "
                f"Allowed: {sorted(ALLOWED_OPERATORS)}"
            )

    def _auto_name(self, normalized: str) -> str:
        """自动生成因子名称（截取前 40 字符）。"""
        clean = re.sub(r"[^a-zA-Z0-9_]", "_", normalized)
        return clean[:40].strip("_") or "unnamed_factor"

    def _build_namespace(
        self,
        data: pd.DataFrame,
        group_col: str,
        datetime_col: str,
    ) -> dict[str, Any]:
        """构建执行命名空间：数据字段 + 运算符函数。"""
        from aipha.factors import operators as ops

        ns: dict[str, Any] = {}

        # 注入数据字段
        group = data[group_col] if group_col in data.columns else None
        dt_group = data[datetime_col] if datetime_col in data.columns else None

        for col in data.columns:
            ns[f"_F_{col}"] = data[col]

        # 注入运算符（带自动 group 绑定）
        for op_name in ALLOWED_OPERATORS:
            func = getattr(ops, op_name, None)
            if func is None:
                continue
            ns[op_name] = self._wrap_operator(
                func, op_name, group, dt_group
            )

        # 注入常用数学
        ns["abs"] = np.abs
        ns["log"] = lambda x: np.log(
            x.where(x > 0, other=np.nan) if isinstance(x, pd.Series) else x
        )
        ns["sign"] = np.sign
        ns["max"] = lambda a, b: ts_max_elem(a, b)
        ns["min"] = lambda a, b: ts_min_elem(a, b)

        return ns

    def _wrap_operator(
        self,
        func: Callable,
        name: str,
        group: Optional[pd.Series],
        dt_group: Optional[pd.Series],
    ) -> Callable:
        """包装运算符，自动注入 group/datetime_group 参数。"""
        import inspect
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())

        if "group" in params and "datetime_group" not in params:
            # ts_* 运算符
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                if "group" not in kwargs and group is not None:
                    kwargs["group"] = group
                return func(*args, **kwargs)
            wrapped.__name__ = name
            return wrapped

        if "datetime_group" in params:
            # cs_* 运算符
            def wrapped_cs(*args: Any, **kwargs: Any) -> Any:
                if "datetime_group" not in kwargs and dt_group is not None:
                    kwargs["datetime_group"] = dt_group
                return func(*args, **kwargs)
            wrapped_cs.__name__ = name
            return wrapped_cs

        return func


def ts_max_elem(a: Any, b: Any) -> pd.Series:
    """逐元素取最大值（辅助函数）。"""
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.concat([a, b], axis=1).max(axis=1)
    if isinstance(a, pd.Series):
        return a.clip(lower=b)
    if isinstance(b, pd.Series):
        return b.clip(lower=a)
    return max(a, b)


def ts_min_elem(a: Any, b: Any) -> pd.Series:
    """逐元素取最小值（辅助函数）。"""
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.concat([a, b], axis=1).min(axis=1)
    if isinstance(a, pd.Series):
        return a.clip(upper=b)
    if isinstance(b, pd.Series):
        return b.clip(upper=a)
    return min(a, b)
