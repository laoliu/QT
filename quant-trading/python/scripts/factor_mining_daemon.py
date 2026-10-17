"""24/7 全自动因子挖掘守护进程 — 基于算法变异 + IC Trap Guard 验证.

通过算子/窗口/字段组合变异，从已验证因子出发持续搜索新因子，
每轮生成候选 → AST 去重 → 沙箱计算 → 月频 IC 验证 → 知识库累积。

用法:
    # 前台运行（带日志）
    python scripts/factor_mining_daemon.py

    # 后台运行
    python scripts/factor_mining_daemon.py --daemon

    # 单轮测试
    python scripts/factor_mining_daemon.py --once

    # 自定义参数
    python scripts/factor_mining_daemon.py --interval 30 --universe csi2000
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import logging
import math
import os
import random
import re
import signal
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from aipha.factors.ast_dedup import ASTFactorDeduplicator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("mining_daemon")

# ── 从字段注册表加载（单一来源: configs/mining/field_registry.yml）──
_REGISTRY_PATH = ROOT / "configs" / "mining" / "field_registry.yml"


def _load_registry() -> dict:
    """加载字段注册表，不存在时返回空 dict。"""
    if not _REGISTRY_PATH.exists():
        logger.warning("字段注册表不存在: %s，使用内置 fallback", _REGISTRY_PATH)
        return {}
    import yaml
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_REG = _load_registry()
_REG_THRESH = _REG.get("guard_thresholds", {})
_REG_DAEMON = _REG.get("daemon", {})

# ── 挖掘常量（优先从注册表取值，fallback 为旧默认）──
IC_THRESHOLD = _REG_THRESH.get("ic_threshold", 0.03)
ICIR_THRESHOLD = _REG_THRESH.get("icir_threshold", 0.40)
POST_IC_THRESHOLD = _REG_THRESH.get("post_ic_threshold", 0.02)
HLZ_T_THRESHOLD = _REG_THRESH.get("hlz_t_threshold", 2.0)
WINNER_ICIR = _REG_THRESH.get("winner_icir", 1.0)
IC_CV_MAX = _REG_THRESH.get("ic_cv_max", 2.0)
SEG_CUTOFF = pd.Timestamp(_REG_DAEMON.get("seg_cutoff", "2022-08-01"))
DEFAULT_INTERVAL_MIN = _REG_DAEMON.get("interval_min", 15)
CANDIDATES_PER_ROUND = _REG_DAEMON.get("candidates_per_round", 20)

# 变异空间 — 从注册表加载字段
_fields_section = _REG.get("fields", {})
PRICE_FIELDS = _fields_section.get("price", ["close", "open", "high", "low", "pre_close"])
VOLUME_FIELDS = _fields_section.get("volume", ["volume", "amount"])
BASIC_FIELDS = _fields_section.get("daily_indicator", [
    "turnover_rate", "pct_chg", "pe_ttm", "pb", "total_mv",
    "circ_mv", "dv_ratio", "dv_ttm",
])
ALL_FIELDS = PRICE_FIELDS + VOLUME_FIELDS + BASIC_FIELDS

WINDOWS = _REG_DAEMON.get("windows", [3, 5, 10, 15, 20, 30, 60, 120])

TS_OPS_UNARY = [
    # 窗口化聚合 (window required)
    "ts_mean", "ts_std", "ts_sum", "ts_min", "ts_max",
    "ts_rank", "ts_decay_linear", "ts_slope",
    "ts_argmax", "ts_argmin", "ts_delta",
    # P2-1 扩展: 回归/分布/累乘
    "ts_var", "ts_rsquare", "ts_resi", "ts_product",
]
# 无窗口一元变换 (直接作用于 Series)
TS_OPS_TRANSFORM = ["ts_log", "ts_abs", "ts_sign"]
TS_OPS_BINARY = ["ts_corr", "ts_cov"]
# P2-1 扩展: 增加截面算子
CS_OPS = ["cs_rank", "cs_zscore", "cs_scale", "cs_mean", "cs_std"]


@dataclass
class KnowledgeEntry:
    """知识库条目."""
    name: str
    expression: str
    category: str
    ic_mean: float = 0.0
    icir: float = 0.0
    post_ic: float = 0.0
    passed: bool = False
    failure_reason: str = ""
    discovered_at: str = ""
    round_id: int = 0
    fitness: float = 0.0
    method: str = ""
    multi_period: dict = field(default_factory=dict)  # P2-3


@dataclass
class DaemonState:
    """守护进程持久化状态."""
    total_rounds: int = 0
    total_candidates: int = 0
    total_passed: int = 0
    total_duplicates: int = 0
    started_at: str = ""
    last_round_at: str = ""
    known_expressions: list[str] = field(default_factory=list)


class PersistentKnowledgeBase:
    """JSON 持久化知识库."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: list[KnowledgeEntry] = []
        self._expr_set: set[str] = set()
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.entries = [KnowledgeEntry(**e) for e in data]
                self._expr_set = {e.expression for e in self.entries}
                logger.info("知识库加载: %d 条记录 (%d 通过)",
                            len(self.entries),
                            sum(1 for e in self.entries if e.passed))
            except Exception as exc:
                logger.warning("知识库加载失败: %s, 重建空库", exc)
                self.entries = []
                self._expr_set = set()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(e) for e in self.entries]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)

    def add_result(self, entry: KnowledgeEntry) -> None:
        self.entries.append(entry)
        self._expr_set.add(entry.expression)

    def get_passed(self) -> list[KnowledgeEntry]:
        return [e for e in self.entries if e.passed]

    def get_all_expressions(self) -> set[str]:
        return set(self._expr_set)

    def get_expression_hashes(self) -> set[str]:
        return {MutationEngine._expr_hash(e) for e in self._expr_set}

    def stats(self) -> dict:
        total = len(self.entries)
        passed = sum(1 for e in self.entries if e.passed)
        return {"total": total, "passed": passed,
                "failed": total - passed,
                "pass_rate": round(passed / max(total, 1), 3)}


class MutationEngine:
    """因子表达式变异引擎 — 6 种变异策略 + Thompson Sampling."""

    # 变异方法名称 → 用于 Thompson Sampling 追踪
    METHOD_NAMES = [
        "window", "field", "operator", "negate", "nest",
        "compose", "fundamental", "interaction", "mcts",
    ]

    def __init__(self, seed_factors: list[dict], rng: random.Random) -> None:
        self.seeds = seed_factors
        self.rng = rng
        self._mutation_methods = {
            "window": self._mutate_window,
            "field": self._mutate_field,
            "operator": self._mutate_operator,
            "negate": self._mutate_negate,
            "nest": self._mutate_nest,
        }
        # P1-3: Thompson Sampling — Beta(α, β) per method
        self._ts_alpha = {m: 1.0 for m in self.METHOD_NAMES}
        self._ts_beta = {m: 1.0 for m in self.METHOD_NAMES}
        # P2-1: MCTS searcher
        self.mcts = MCTSSearcher(rng)

    def generate_batch(self, n: int, known_hashes: set[str]) -> list[dict]:
        """生成 n 个去重候选 (Thompson Sampling + 字段多样性配额)."""
        candidates: list[dict] = []
        attempts = 0
        max_attempts = n * 8
        # 字段多样性配额: 至少 30% 候选必须使用基本面/量字段
        non_price_quota = max(n * 3 // 10, 4)
        non_price_count = 0
        interaction_quota = max(n * 15 // 100, 2)  # 15% 交互因子
        interaction_count = 0
        while len(candidates) < n and attempts < max_attempts:
            attempts += 1
            # 如果基本面配额未达标且剩余名额不多，强制使用 fundamental
            remaining = n - len(candidates)
            need_nonprice = non_price_quota - non_price_count
            need_interaction = interaction_quota - interaction_count
            force_fundamental = (need_nonprice > 0
                                 and remaining <= need_nonprice + 2)
            force_interaction = (need_interaction > 0
                                 and remaining <= need_interaction + 1
                                 and not force_fundamental)
            if force_interaction:
                method_name = "interaction"
            elif force_fundamental:
                method_name = "fundamental"
            else:
                method_name = self._thompson_select()
            new_expr, mcts_path = self._dispatch(method_name)
            if new_expr is None:
                continue
            h = self._expr_hash(new_expr)
            if h in known_hashes:
                continue
            known_hashes.add(h)
            name = f"mut_r{self.rng.randint(0,9999):04d}_{len(candidates)}"
            cand: dict = {
                "name": name,
                "expression": new_expr,
                "category": "mutation",
                "method": method_name,
            }
            if mcts_path:
                cand["mcts_path"] = mcts_path
            candidates.append(cand)
            # 追踪是否含基本面字段
            if any(f'${f}' in new_expr for f in BASIC_FIELDS):
                non_price_count += 1
            if method_name == "interaction":
                interaction_count += 1
            # 追踪探索频率 (用于日志/监控)
            self._track_exploration(new_expr)
        return candidates

    def _thompson_select(self) -> str:
        """P1-3: Thompson Sampling — 从 Beta 分布采样选最优方法."""
        best_method = ""
        best_sample = -1.0
        for m in self.METHOD_NAMES:
            s = self.rng.betavariate(self._ts_alpha[m], self._ts_beta[m])
            if s > best_sample:
                best_sample = s
                best_method = m
        return best_method

    def _dispatch(self, method_name: str) -> tuple[Optional[str], list[str]]:
        """根据方法名分发执行. Returns (expr, mcts_path)."""
        if method_name == "mcts":
            expr, path = self.mcts.generate_one()
            return expr, path
        if method_name == "fundamental":
            return self._fresh_fundamental(), []
        if method_name == "interaction":
            return self._fresh_interaction(), []
        if method_name == "compose":
            if len(self.seeds) < 2:
                return None, []
            s1, s2 = self.rng.sample(self.seeds, 2)
            return self._mutate_compose(
                s1["expression"], s2["expression"]), []
        fn = self._mutation_methods.get(method_name)
        if fn is None:
            return None, []
        seed = self.rng.choice(self.seeds)
        return fn(seed["expression"]), []

    def update_outcome(self, method: str, passed: bool) -> None:
        """P1-3: 更新 Thompson Sampling 后验."""
        if method in self._ts_alpha:
            if passed:
                self._ts_alpha[method] += 1.0
            else:
                self._ts_beta[method] += 1.0

    def get_ts_stats(self) -> dict[str, dict]:
        """获取 Thompson Sampling 统计."""
        result = {}
        for m in self.METHOD_NAMES:
            a, b = self._ts_alpha[m], self._ts_beta[m]
            result[m] = {
                "alpha": a, "beta": b,
                "mean": round(a / (a + b), 3),
                "trials": int(a + b - 2),
            }
        return result

    def decay_priors(self, factor: float = 0.97) -> None:
        """P1-3: Thompson prior 指数衰减 — 防止历史优势无限累积.

        每轮乘以 factor(0.97), floor=1.0 (回到无信息先验).
        30 轮后有效记忆 ≈ 0.97^30 ≈ 0.40 (旧信号权重衰减到 40%).
        """
        for m in self.METHOD_NAMES:
            self._ts_alpha[m] = max(1.0, self._ts_alpha[m] * factor)
            self._ts_beta[m] = max(1.0, self._ts_beta[m] * factor)

    def _mutate_window(self, expr: str) -> Optional[str]:
        """替换一个数字窗口参数."""
        nums = list(re.finditer(r'(?<=,\s)\d+(?=\))', expr))
        if not nums:
            return None
        target = self.rng.choice(nums)
        old_val = int(target.group())
        new_val = self.rng.choice([w for w in WINDOWS if w != old_val])
        return expr[:target.start()] + str(new_val) + expr[target.end():]

    def _mutate_field(self, expr: str) -> Optional[str]:
        """替换一个 $field 引用 (50% 跨类替换, 加速基本面覆盖)."""
        fields = list(re.finditer(r'\$(\w+)', expr))
        if not fields:
            return None
        target = self.rng.choice(fields)
        old_f = target.group(1)
        # 50% 跨类替换（从 30% 提升, 缓解 $close 55% 集中度）
        cross_category = self.rng.random() < 0.50
        if cross_category:
            pool = [f for f in ALL_FIELDS if f != old_f]
        elif old_f in PRICE_FIELDS:
            pool = [f for f in PRICE_FIELDS if f != old_f]
        elif old_f in VOLUME_FIELDS:
            pool = [f for f in VOLUME_FIELDS if f != old_f]
        else:
            pool = [f for f in BASIC_FIELDS if f != old_f]
        if not pool:
            return None
        new_f = self.rng.choice(pool)
        s, e = target.start(), target.end()
        return expr[:s] + "$" + new_f + expr[e:]

    def _mutate_operator(self, expr: str) -> Optional[str]:
        """替换一个时序算子."""
        ops = list(re.finditer(r'\b(ts_\w+)\(', expr))
        if not ops:
            return None
        target = self.rng.choice(ops)
        old_op = target.group(1)
        if old_op in TS_OPS_UNARY:
            pool = [o for o in TS_OPS_UNARY if o != old_op]
        elif old_op in TS_OPS_BINARY:
            pool = [o for o in TS_OPS_BINARY if o != old_op]
        else:
            return None
        if not pool:
            return None
        new_op = self.rng.choice(pool)
        s, e = target.start(1), target.end(1)
        return expr[:s] + new_op + expr[e:]

    def _mutate_negate(self, expr: str) -> Optional[str]:
        """切换正负号."""
        # 如果已经以 cs_rank(- 开头，去掉负号
        if "cs_rank(-" in expr:
            return expr.replace("cs_rank(-", "cs_rank(", 1)
        if "cs_rank(" in expr:
            return expr.replace("cs_rank(", "cs_rank(-", 1)
        if expr.startswith("-"):
            return expr[1:]
        return "-(" + expr + ")"

    def _mutate_compose(self, expr1: str, expr2: str) -> Optional[str]:
        """组合两个因子: safe_div / 乘积 / 差."""
        # 去掉外层 cs_rank
        e1 = re.sub(r'^cs_rank\((.*)\)$', r'\1', expr1)
        e2 = re.sub(r'^cs_rank\((.*)\)$', r'\1', expr2)
        ops = [
            f"cs_rank(safe_div({e1}, {e2} + 1e-8))",
            f"cs_rank(({e1}) * ({e2}))",
            f"cs_rank(({e1}) - ({e2}))",
        ]
        return self.rng.choice(ops)

    def _mutate_nest(self, expr: str) -> Optional[str]:
        """在外层嵌套一个新的时序算子或变换."""
        inner = re.sub(r'^cs_rank\((.*)\)$', r'\1', expr)
        # 20% 概率用无窗口变换, 80% 用窗口算子
        if self.rng.random() < 0.20 and TS_OPS_TRANSFORM:
            transform = self.rng.choice(TS_OPS_TRANSFORM)
            return f"cs_rank({transform}({inner}))"
        w = self.rng.choice([5, 10, 20])
        op = self.rng.choice(["ts_mean", "ts_std", "ts_delta",
                              "ts_resi", "ts_rsquare", "ts_var"])
        return f"cs_rank({op}({inner}, {w}))"

    def _fresh_fundamental(self) -> str:
        """P1-2: 生成全新基本面因子模板."""
        f1 = self.rng.choice(BASIC_FIELDS)
        f2 = self.rng.choice(ALL_FIELDS)
        w = self.rng.choice(WINDOWS)
        op = self.rng.choice(TS_OPS_UNARY)
        patterns = [
            f"cs_rank({op}(${f1}, {w}))",
            f"cs_rank(-{op}(${f1}, {w}))",
            f"cs_rank(safe_div(${f1}, ${f2} + 1e-8))",
            f"cs_rank(ts_corr(${f1}, ${f2}, {w}))",
            f"cs_rank(ts_delta(${f1}, {w}))",
            f"cs_rank(safe_div(${f1}, ts_mean(${f1}, {w}) + 1e-8))",
            # P2-1: 使用新算子
            f"cs_rank(ts_resi(${f1}, {w}))",
            f"cs_rank(ts_rsquare(${f1}, {w}))",
            f"cs_rank(ts_product(${f1}, {min(w, 10)}))",
            f"cs_rank(ts_log(ts_abs(${f1})))",
            # P2-2: $open 特有模式 (隔夜收益/日内结构)
            f"cs_rank(safe_div($open - ts_delay($close, 1), "
            f"ts_delay($close, 1) + 1e-8))",
            f"cs_rank(ts_mean(safe_div($open, $close + 1e-8), {w}))",
            f"cs_rank(safe_div($close - $low, $high - $low + 1e-8))",
        ]
        return self.rng.choice(patterns)

    def _fresh_interaction(self) -> str:
        """生成跨域交互因子: 基本面 × 价格/量 交叉 (AlphaSAGE 启发)."""
        fund = self.rng.choice(BASIC_FIELDS)
        price = self.rng.choice(PRICE_FIELDS + VOLUME_FIELDS)
        w1 = self.rng.choice(WINDOWS)
        w2 = self.rng.choice([w for w in WINDOWS if w != w1] or WINDOWS)
        op1 = self.rng.choice(TS_OPS_UNARY)
        op2 = self.rng.choice(TS_OPS_UNARY)
        patterns = [
            f"cs_rank(ts_corr(${fund}, ${price}, {w1}))",
            f"cs_rank(ts_cov(${fund}, ${price}, {w1}))",
            f"cs_rank(safe_div({op1}(${fund}, {w1}), "
            f"{op2}(${price}, {w2}) + 1e-8))",
            f"cs_rank({op1}(${fund}, {w1}) * {op2}(${price}, {w2}))",
            f"cs_rank(ts_delta(safe_div(${fund}, ${price} + 1e-8), {w1}))",
            f"cs_rank({op1}(${fund} * ${price}, {w1}))",
        ]
        return self.rng.choice(patterns)

    def _track_exploration(self, expr: str) -> None:
        """追踪已探索的 op×field 组合频率."""
        if not hasattr(self, '_explore_counts'):
            self._explore_counts: dict[str, int] = {}
        for m in re.finditer(r'(ts_\w+)\(\$(\w+)', expr):
            key = f"{m.group(1)}_{m.group(2)}"
            self._explore_counts[key] = self._explore_counts.get(key, 0) + 1

    def get_exploration_stats(self) -> dict[str, int]:
        """获取探索频率统计."""
        if not hasattr(self, '_explore_counts'):
            return {}
        return dict(sorted(self._explore_counts.items(),
                           key=lambda x: -x[1])[:15])

    def add_winners(self, winners: list[dict]) -> int:
        """P1-1: 高质量因子回流为新种子. Returns added count."""
        existing = {s["expression"] for s in self.seeds}
        added = 0
        for w in winners:
            if w["expression"] not in existing:
                self.seeds.append(w)
                existing.add(w["expression"])
                added += 1
        return added

    @staticmethod
    def _expr_hash(expr: str) -> str:
        """表达式规范化哈希（忽略空格）."""
        clean = re.sub(r'\s+', '', expr)
        return hashlib.md5(clean.encode()).hexdigest()[:12]


# ── MCTS 定向搜索 (P2-1) ──


class MCTSNode:
    """MCTS 树节点."""
    __slots__ = ("action", "children", "visits", "reward")

    def __init__(self, action: str = "root") -> None:
        self.action = action
        self.children: dict[str, "MCTSNode"] = {}
        self.visits: int = 0
        self.reward: float = 0.0


class MCTSSearcher:
    """UCB1 引导的因子空间搜索 — 4 层树: pattern → operator → field → window.

    支持 5 种模板:
      simple:  cs_rank(op($field, window))
      negate:  -cs_rank(op($field, window))
      binary:  cs_rank(bin_op($f1, $f2, window))
      compose: cs_rank(op1(op2($field, w_inner), w_outer))
      interaction: cs_rank(op($fundamental * $price_vol, window))

    新增 (P3-1): 频繁子树惩罚 — 高访问低奖励路径降权，避免反复探索失败区域。
    """

    PATTERN_TYPES = ["simple", "negate", "binary", "compose", "interaction"]
    UCB_C = 1.414
    PENALTY_THRESHOLD = 10  # 访问次数超过此值且平均奖励低的节点被惩罚

    def __init__(self, rng: random.Random) -> None:
        self.root = MCTSNode()
        self.rng = rng

    def generate_one(self) -> tuple[str, list[str]]:
        """生成一个表达式 + 搜索路径."""
        path: list[str] = []
        node = self.root
        # Level 0: pattern type
        ptype = self._select(node, self.PATTERN_TYPES)
        path.append(ptype)
        node = node.children[ptype]

        if ptype in ("simple", "negate"):
            op = self._select(node, TS_OPS_UNARY)
            path.append(op)
            node = node.children[op]
            fld = self._select(node, ALL_FIELDS)
            path.append(fld)
            node = node.children[fld]
            w = self._select(node, [str(x) for x in WINDOWS])
            path.append(w)
            inner = f"{op}(${fld}, {w})"
            expr = (f"-cs_rank({inner})" if ptype == "negate"
                    else f"cs_rank({inner})")
        elif ptype == "binary":
            bin_op = self._select(node, TS_OPS_BINARY)
            path.append(bin_op)
            node = node.children[bin_op]
            f1 = self._select(node, ALL_FIELDS)
            path.append(f1)
            node = node.children[f1]
            f2_pool = [f for f in ALL_FIELDS if f != f1] or ALL_FIELDS
            f2 = self._select(node, f2_pool)
            path.append(f2)
            node = node.children[f2]
            w = self._select(node, [str(x) for x in WINDOWS])
            path.append(w)
            expr = f"cs_rank({bin_op}(${f1}, ${f2}, {w}))"
        elif ptype == "compose":
            op1 = self._select(node, TS_OPS_UNARY)
            path.append(op1)
            node = node.children[op1]
            op2 = self._select(node, TS_OPS_UNARY)
            path.append(op2)
            node = node.children[op2]
            fld = self._select(node, ALL_FIELDS)
            path.append(fld)
            node = node.children[fld]
            w_out = self._select(node, [str(x) for x in WINDOWS])
            path.append(w_out)
            w_in = self.rng.choice(
                [x for x in WINDOWS if x <= int(w_out)] or WINDOWS[:3])
            expr = f"cs_rank({op1}({op2}(${fld}, {w_in}), {w_out}))"
        elif ptype == "interaction":
            # 交互: op($fundamental × $price_vol, window)
            op = self._select(node, TS_OPS_UNARY)
            path.append(op)
            node = node.children[op]
            fund = self._select(node, BASIC_FIELDS)
            path.append(fund)
            node = node.children[fund]
            pv = self._select(node, PRICE_FIELDS + VOLUME_FIELDS)
            path.append(pv)
            node = node.children[pv]
            w = self._select(node, [str(x) for x in WINDOWS])
            path.append(w)
            combo_ops = [
                f"cs_rank({op}(${fund} * ${pv}, {w}))",
                f"cs_rank(safe_div({op}(${fund}, {w}), "
                f"{op}(${pv}, {w}) + 1e-8))",
                f"cs_rank(ts_corr(${fund}, ${pv}, {w}))",
            ]
            expr = self.rng.choice(combo_ops)
        return expr, path

    def backpropagate(self, path: list[str], reward: float) -> None:
        """回传奖励至路径上所有节点."""
        node = self.root
        node.visits += 1
        node.reward += reward
        for action in path:
            child = node.children.get(action)
            if child is None:
                break
            child.visits += 1
            child.reward += reward
            node = child

    def _select(
        self, parent: MCTSNode, actions: list[str],
    ) -> str:
        """UCB1 选择 with 频繁子树惩罚 (LLM+MCTS 风格)."""
        unvisited = [a for a in actions
                     if a not in parent.children]
        if unvisited:
            a = self.rng.choice(unvisited)
            parent.children[a] = MCTSNode(a)
            return a
        best_a, best_ucb = "", -1.0
        pv = max(parent.visits, 1)
        for a in actions:
            child = parent.children.get(a)
            if child is None or child.visits == 0:
                continue
            avg_reward = child.reward / child.visits
            exploit = avg_reward
            explore = self.UCB_C * math.sqrt(
                math.log(pv) / child.visits)
            # P3-1: 频繁子树惩罚 — 高访问低奖励路径降权
            penalty = 0.0
            if (child.visits >= self.PENALTY_THRESHOLD
                    and avg_reward < 0.1):
                penalty = 0.3 * math.log(1 + child.visits)
            ucb = exploit + explore - penalty
            if ucb > best_ucb:
                best_ucb = ucb
                best_a = a
        return best_a or self.rng.choice(actions)


# ── 因子质量把关管道 (P0-1: ICTrapGuard 集成) ──

class FactorGuardPipeline:
    """因子质量把关管道 — AST 去重 + IC 时序相关性 + HLZ 多重检验校正.

    三阶段检查:
    1. AST 结构去重 — 拒绝与已有因子高度相似的表达式
    2. IC 时序相关性 — 拒绝月频 IC 序列与已有因子高度相关的因子
    3. HLZ 校正 — 按累计试验数施加多重检验惩罚
    """

    def __init__(
        self,
        ast_sim_threshold: float = 0.85,
        ic_corr_threshold: float = 0.70,
        hlz_t_threshold: float = 2.0,
    ) -> None:
        self.dedup = ASTFactorDeduplicator(
            similarity_threshold=ast_sim_threshold,
        )
        self.ic_corr_threshold = ic_corr_threshold
        self.hlz_t_threshold = hlz_t_threshold
        self.total_trials: int = 0
        self._passed_ics: dict[str, dict] = {}  # {name: {"ics": [...], "icir": float}}
        # 被 ic_corr 拒绝的高价值因子（ICIR > WINNER_ICIR）
        self.rejected_gems: list[dict] = []
        # P0-1: 条件替换记录
        self.replacements: list[dict] = []
        self.REPLACE_RATIO = 1.3  # 新因子 ICIR 须超过旧因子 30%

    def register_passed(
        self, name: str, expression: str, ic_records: list[dict],
        icir: float = 0.0,
    ) -> None:
        """注册已通过因子到去重库."""
        self.dedup.register(name, expression)
        self._passed_ics[name] = {
            "ics": [r["ic"] for r in ic_records],
            "icir": icir,
        }

    def check(
        self, name: str, expression: str,
        ic_records: list[dict], t_stat: float,
    ) -> tuple[bool, str]:
        """三阶段检查. Returns (passed, failure_reason)."""
        self.total_trials += 1
        # Stage 1: AST 结构去重
        res = self.dedup.check_duplicate(name, expression)
        if res.is_duplicate:
            return False, (
                f"ast_dup({res.most_similar_name}"
                f":{res.similarity_score:.2f})"
            )
        # Stage 2: IC 时序相关性
        new_ics = [r["ic"] for r in ic_records]
        if len(new_ics) >= 6 and self._passed_ics:
            max_corr, max_name = 0.0, ""
            arr_new = np.array(new_ics)
            for ex_name, ex_data in self._passed_ics.items():
                ex_ics = ex_data["ics"]
                n = min(len(arr_new), len(ex_ics))
                if n < 6:
                    continue
                c = float(np.corrcoef(
                    arr_new[:n], ex_ics[:n],
                )[0, 1])
                if np.isfinite(c) and abs(c) > abs(max_corr):
                    max_corr, max_name = c, ex_name
            if abs(max_corr) >= self.ic_corr_threshold:
                # P0-1: 条件替换 — 新因子 ICIR > 旧因子 * 1.3 时替换
                ics_arr = np.array(new_ics)
                new_icir = (float(np.mean(ics_arr))
                            / max(float(np.std(ics_arr, ddof=1)), 1e-12))
                old_data = self._passed_ics[max_name]
                old_icir = old_data.get("icir", 0.0)
                can_replace = (
                    abs(old_icir) > 0
                    and abs(new_icir) > abs(old_icir) * self.REPLACE_RATIO
                )
                if can_replace:
                    logger.info(
                        "🔄 REPLACE [%s](ICIR=%.3f) → "
                        "[%s](ICIR=%.3f), corr=%.2f",
                        max_name, old_icir, name, new_icir, max_corr)
                    del self._passed_ics[max_name]
                    self.replacements.append({
                        "old_name": max_name,
                        "old_icir": round(old_icir, 3),
                        "new_name": name,
                        "new_icir": round(new_icir, 3),
                        "corr": round(max_corr, 3),
                    })
                    # 不 return — 继续到 Stage 3 (HLZ)
                else:
                    reason = f"ic_corr({max_name}:{max_corr:.2f})"
                    if abs(new_icir) >= WINNER_ICIR:
                        self.rejected_gems.append({
                            "name": name, "expression": expression,
                            "icir": round(new_icir, 3),
                            "corr_with": max_name,
                            "corr_val": round(max_corr, 3),
                        })
                    return False, reason
        # Stage 3: HLZ 多重检验校正
        # 修正(P0-1): 使用总试验数而非通过数 — HLZ(2016) penalty
        # 覆盖全部 N 个试验, 否则低估多重检验惩罚
        n_total = max(self.total_trials, 1)
        penalty = np.sqrt(
            2.0 * np.log(n_total)
        )
        t_adj = abs(t_stat) - penalty
        if t_adj < self.hlz_t_threshold:
            return False, (
                f"hlz(t_adj={t_adj:.2f}<{self.hlz_t_threshold})"
            )
        return True, ""

    def compute_ic_novelty(self, ic_records: list[dict]) -> float:
        """P2-2: 新颖度 = 1 - max|IC相关性| (with existing passed)."""
        if not self._passed_ics:
            return 1.0
        new_ics = [r["ic"] for r in ic_records]
        if len(new_ics) < 6:
            return 1.0
        max_corr = 0.0
        arr = np.array(new_ics)
        for ex_data in self._passed_ics.values():
            ex_ics = ex_data["ics"]
            n = min(len(arr), len(ex_ics))
            if n < 6:
                continue
            c = float(np.corrcoef(arr[:n], ex_ics[:n])[0, 1])
            if np.isfinite(c) and abs(c) > abs(max_corr):
                max_corr = c
        return 1.0 - abs(max_corr)

    def compute_diversity_entropy(self) -> float:
        """P1-2: 因子池多样性熵 (AlphaEval DH).

        DH = -sum(p_i * log(p_i)) / log(m)
        其中 p_i = lambda_i / sum(lambda), lambda 是 IC 协方差矩阵的特征值.
        DH ∈ [0, 1], 1 = 完全分散, 0 = 全部相同.
        """
        if len(self._passed_ics) < 2:
            return 1.0
        # 构造 IC 矩阵: 每行一个因子, 每列一个月
        names = list(self._passed_ics.keys())
        all_ics = [self._passed_ics[n]["ics"] for n in names]
        min_len = min(len(x) for x in all_ics)
        if min_len < 3:
            return 1.0
        mat = np.array([x[:min_len] for x in all_ics])  # (m, T)
        # 协方差矩阵的特征值
        cov = np.cov(mat)  # (m, m)
        eigvals = np.linalg.eigvalsh(cov)
        eigvals = eigvals[eigvals > 1e-15]  # 过滤数值零
        if len(eigvals) < 1:
            return 0.0
        p = eigvals / eigvals.sum()
        m = len(p)
        if m == 1:
            return 0.0
        entropy = -float(np.sum(p * np.log(p)))
        dh = entropy / np.log(m)
        return round(float(np.clip(dh, 0.0, 1.0)), 4)


def load_market_data(universe: str, start: str, end: str,
                     fwd: int,
                     fwd_periods: list[int] | None = None) -> pd.DataFrame:
    """加载市场数据并计算前向收益.

    Args:
        fwd: 主前向周期 (兼容旧接口).
        fwd_periods: 全部前向周期列表. 若提供则计算所有周期.
                     若 None, 退化为仅计算 fwd.
    """
    from train_ml_factor import (
        load_daily_data, filter_universe,
        _load_fina_indicator_bulk, _pit_merge_fina,
    )
    logger.info("加载市场数据: %s ~ %s, universe=%s", start, end, universe)
    df = load_daily_data(start, end)
    df = filter_universe(df, universe)

    # PIT 合并财务数据（ROE/ROA/利润率等）以扩展挖掘搜索空间
    cache_dir = ROOT / "data" / "cache" / "equity_tushare"
    symbols = df["symbol"].unique().tolist()
    fina = _load_fina_indicator_bulk(cache_dir, symbols)
    if not fina.empty:
        df = _pit_merge_fina(df, fina)
        n_fina = sum(1 for c in df.columns if c in (
            "roe", "roa", "grossprofit_margin", "netprofit_margin",
            "debt_to_assets", "current_ratio", "quick_ratio",
            "dt_netprofit_yoy", "op_yoy", "cfps", "eps", "bps",
        ))
        logger.info("接入 %d 个财务字段到挖掘管道", n_fina)
    else:
        logger.warning("无财务数据可合并，仅使用价量+估值字段")

    periods = fwd_periods or [fwd]
    for p in periods:
        col = f"fwd_{p}d"
        df[col] = df.groupby("symbol")["close"].transform(
            lambda s, _p=p: s.shift(-_p) / s - 1.0
        )
    logger.info("数据就绪: %d行, %d股, fwd_periods=%s",
                len(df), df["symbol"].nunique(), periods)
    return df


def compute_monthly_ic(df: pd.DataFrame, fcol: str,
                       fwd_col: str) -> list[dict]:
    """月频截面 Spearman IC."""
    records: list[dict] = []
    sub = df[[fcol, fwd_col, "datetime"]].dropna()
    if sub.empty:
        return records
    sub = sub.copy()
    sub["ym"] = sub["datetime"].dt.to_period("M")
    for ym, grp in sub.groupby("ym"):
        if len(grp) < 30:
            continue
        fv, rv = grp[fcol].values, grp[fwd_col].values
        if np.std(fv) < 1e-12 or np.std(rv) < 1e-12:
            continue
        ic, _ = sp_stats.spearmanr(fv, rv)
        if np.isfinite(ic):
            records.append({"period": str(ym), "ic": float(ic)})
    return records


def calc_ic_stats(records: list[dict]) -> dict:
    """月度 IC 汇总统计."""
    if not records:
        return {"ic_mean": 0, "icir": 0, "n": 0, "t": 0}
    ics = [r["ic"] for r in records]
    n = len(ics)
    mu = float(np.mean(ics))
    sd = float(np.std(ics, ddof=1)) if n > 1 else 0.0
    icir = mu / sd if sd > 1e-12 else 0.0
    t = mu / (sd / np.sqrt(n)) if sd > 0 else 0.0
    ic_cv = sd / abs(mu) if abs(mu) > 1e-12 else 99.0
    return {"ic_mean": round(mu, 5), "icir": round(icir, 3),
            "n": n, "t": round(float(t), 2),
            "ic_cv": round(ic_cv, 3)}


# ── 已知因子正交化（Market/Size/Value 回归残差）────────────


def orthogonalize_against_known(
    df: pd.DataFrame,
    factor_col: str,
    known_cols: list[str] | None = None,
) -> pd.Series:
    """对因子值做已知因子截面回归残差化。

    对每个截面日期，将 factor_col 对 known_cols 做 OLS 回归，
    取残差作为正交化后的因子值。

    默认控制变量: total_mv (市值/Size), pb (账面市值比/Value), pct_chg (动量)
    """
    if known_cols is None:
        known_cols = ["total_mv", "pb", "pct_chg"]
    # 过滤掉数据中不存在的控制变量
    available = [c for c in known_cols if c in df.columns]
    if not available:
        logger.warning("无可用控制变量，跳过正交化")
        return df[factor_col]

    residuals = pd.Series(np.nan, index=df.index, dtype=float)
    for dt, grp in df.groupby("datetime"):
        cols = [factor_col] + available
        sub = grp[cols].dropna()
        if len(sub) < 30:
            # 样本太少，保留原值
            residuals.loc[grp.index] = grp[factor_col]
            continue
        y = sub[factor_col].values
        X = sub[available].values
        # 加截距项
        X_with_const = np.column_stack([np.ones(len(X)), X])
        try:
            # OLS: beta = (X'X)^-1 X'y
            beta = np.linalg.lstsq(X_with_const, y, rcond=None)[0]
            resid = y - X_with_const @ beta
            residuals.loc[sub.index] = resid
        except np.linalg.LinAlgError:
            residuals.loc[grp.index] = grp[factor_col]
    return residuals


def compute_fitness(
    icir: float, novelty: float, stability: float,
    expr_len: int,
    w: tuple[float, ...] = (0.50, 0.20, 0.20, 0.10),
) -> float:
    """P2-2: 多目标适应度 — ICIR + novelty + stability - complexity.

    Args:
        icir: |ICIR| 绝对值 (越高越好)
        novelty: 1 - max_ic_corr (越接近 1 越新颖)
        stability: post_ic / ic_mean (时间一致性, ≈1.0 为理想)
        expr_len: 表达式字符长度 (越短越好)
        w: (icir, novelty, stability, simplicity) 权重
    """
    simplicity = max(0.0, 1.0 - expr_len / 200.0)
    score = (w[0] * abs(icir)
             + w[1] * max(novelty, 0.0)
             + w[2] * min(abs(stability), 2.0)
             + w[3] * simplicity)
    return round(score, 4)


def run_mining_round(
    round_id: int, df: pd.DataFrame, fwd_col: str,
    engine: MutationEngine, kb: PersistentKnowledgeBase,
    parser: Any, guard: Any, batch_size: int = CANDIDATES_PER_ROUND,
    extra_fwd_cols: list[str] | None = None,
) -> dict:
    """执行单轮变异挖掘."""
    t0 = time.time()
    # P1-3: Thompson prior 衰减 — 防止方法选择固化
    engine.decay_priors()
    known_hashes = kb.get_expression_hashes()
    candidates = engine.generate_batch(batch_size, known_hashes)
    stats = {"round": round_id, "generated": len(candidates),
             "parsed": 0, "evaluated": 0, "passed": 0, "errors": 0,
             "guard_rejected": 0}
    for cand in candidates:
        name, expr = cand["name"], cand["expression"]
        try:
            parsed = parser.parse(expr, name)
        except Exception:
            stats["errors"] += 1
            continue
        stats["parsed"] += 1
        try:
            series = parser.execute(parsed, df)
        except Exception:
            stats["errors"] += 1
            continue
        valid = series.dropna()
        if len(valid) < 100:
            stats["errors"] += 1
            continue
        fcol = f"_fac_{name}"
        df[fcol] = series
        records = compute_monthly_ic(df, fcol, fwd_col)
        ic_s = calc_ic_stats(records)
        # NOTE: defer df.drop(fcol) until after multi-period IC calc
        stats["evaluated"] += 1
        # 分段: post-2022
        post_recs = [r for r in records if r["period"] >= "2022-08"]
        post_ic = calc_ic_stats(post_recs)["ic_mean"] if post_recs else 0
        # ── 基础 IC/ICIR 门槛 ──
        basic_ok = (abs(ic_s["ic_mean"]) >= IC_THRESHOLD
                    and abs(ic_s["icir"]) >= ICIR_THRESHOLD
                    and abs(post_ic) >= POST_IC_THRESHOLD)
        # P1-1: IC 稳定性检查 (IC_CV > 2.0 表明 IC 序列波动过大)
        ic_cv = ic_s.get("ic_cv", 99.0)
        if basic_ok and ic_cv > IC_CV_MAX:
            basic_ok = False
        ok = basic_ok
        if not basic_ok:
            failure_reason = (
                f"ic_cv_unstable({ic_cv:.1f}>{IC_CV_MAX})"
                if ic_cv > IC_CV_MAX
                else "ic/icir below threshold"
            )
        else:
            failure_reason = ""
        # ── 已知因子正交化检查（控制 Size/Value/Momentum 后残差 IC）──
        orth_ic_mean = 0.0
        if basic_ok:
            orth_series = orthogonalize_against_known(df, fcol)
            orth_col = f"_orth_{name}"
            df[orth_col] = orth_series
            orth_recs = compute_monthly_ic(df, orth_col, fwd_col)
            orth_stats = calc_ic_stats(orth_recs)
            orth_ic_mean = orth_stats["ic_mean"]
            df.drop(columns=[orth_col], inplace=True, errors="ignore")
            # 正交化后 |IC| < IC_THRESHOLD → 因子 Alpha 全部来自已知因子
            if abs(orth_ic_mean) < IC_THRESHOLD:
                ok = False
                failure_reason = (
                    f"known_factor_variant(raw_ic={ic_s['ic_mean']:.4f},"
                    f"orth_ic={orth_ic_mean:.4f})"
                )
                logger.info("✗ ORTH [R%d] %s: %s",
                            round_id, name, failure_reason)
        # ── Guard Pipeline: AST 去重 + IC 相关性 + HLZ ──
        if basic_ok and guard is not None:
            ok, failure_reason = guard.check(
                name, expr, records, ic_s["t"])
            if ok:
                guard.register_passed(name, expr, records,
                                      icir=ic_s["icir"])
            else:
                stats["guard_rejected"] += 1
                logger.info("✗ GUARD [R%d] %s: %s",
                            round_id, name, failure_reason)
        # ── P2-2: Multi-objective fitness ──
        novelty = (guard.compute_ic_novelty(records)
                   if guard is not None else 1.0)
        # stability: post_ic 与 ic_mean 同号 → 方向一致 (理想 ≈1.0)
        # 异号 → 方向翻转 (衰减/失效), stability 为 0
        if abs(ic_s["ic_mean"]) > 1e-10:
            raw_stab = post_ic / ic_s["ic_mean"]
            stability = max(raw_stab, 0.0)  # 异号 → 0, 同号 → 正比值
        else:
            stability = 0.0
        fitness = compute_fitness(
            ic_s["icir"], novelty, stability, len(expr))
        # ── P2-3: 多周期前向收益交叉验证 ──
        multi_period: dict[str, dict] = {}
        if extra_fwd_cols:
            for efcol in extra_fwd_cols:
                e_recs = compute_monthly_ic(df, fcol, efcol)
                e_stats = calc_ic_stats(e_recs)
                multi_period[efcol] = {
                    "icir": e_stats["icir"],
                    "ic_mean": e_stats["ic_mean"],
                    "n": e_stats["n"],
                }
            # 多周期一致性加分: 如果 ≥2 个周期 |ICIR|>0.5 则 fitness +5%
            n_consistent = sum(
                1 for v in multi_period.values()
                if abs(v["icir"]) > 0.5)
            if n_consistent >= 2:
                fitness = round(fitness * 1.05, 4)
        # 清理临时因子列 (延后到多周期计算完成后)
        df.drop(columns=[fcol], inplace=True, errors="ignore")
        entry = KnowledgeEntry(
            name=name, expression=expr, category="mutation",
            ic_mean=ic_s["ic_mean"], icir=ic_s["icir"],
            post_ic=post_ic, passed=ok,
            failure_reason=failure_reason,
            discovered_at=datetime.now().isoformat(),
            round_id=round_id,
            fitness=fitness,
            method=cand.get("method", ""),
            multi_period=multi_period)
        kb.add_result(entry)
        if ok:
            stats["passed"] += 1
            logger.info(
                "✓ PASS [R%d] %s  ICIR=%.3f  fitness=%.3f",
                round_id, name, ic_s["icir"], fitness)
        # P1-3: Thompson Sampling 反馈
        # 使用 basic_ok（IC/ICIR门槛）而非 ok（含Guard去重）
        # 方法质量 = "能否产生有效因子"，而非"是否足够新颖"
        method = cand.get("method", "")
        if method:
            engine.update_outcome(method, basic_ok)
        # P2-1: MCTS 回传 (use fitness as reward)
        # 失败因子也给小量reward，区分"差一点"与"完全无效"
        mcts_path = cand.get("mcts_path")
        if mcts_path:
            reward = fitness if ok else fitness * 0.1
            engine.mcts.backpropagate(mcts_path, reward)
    kb.save()
    # ── P1-1: Winner breeding — 高 ICIR 因子回流为种子 ──
    round_passed = [e for e in kb.entries[-stats["evaluated"]:]
                    if e.passed and abs(e.icir) >= WINNER_ICIR]
    if round_passed:
        new_seeds = [{"name": e.name, "expression": e.expression}
                     for e in round_passed]
        added = engine.add_winners(new_seeds)
        if added:
            logger.info("🧬 Breeding: %d winners → seeds (total=%d)",
                        added, len(engine.seeds))
    elapsed = time.time() - t0
    stats["elapsed_sec"] = round(elapsed, 1)
    logger.info(
        "Round %d done: %d gen → %d eval → %d pass, "
        "%d guard_rejected (%.1fs)",
        round_id, stats["generated"], stats["evaluated"],
        stats["passed"], stats["guard_rejected"], elapsed)
    # P1-3: Log Thompson Sampling stats
    ts = engine.get_ts_stats()
    top3 = sorted(ts.items(), key=lambda x: x[1]["mean"], reverse=True)[:3]
    logger.info("TS top-3: %s",
                ", ".join(f"{m}={s['mean']:.2f}({s['trials']})"
                          for m, s in top3))
    # P1-2: Log Diversity Entropy (DH)
    if guard is not None and len(guard._passed_ics) >= 2:
        dh = guard.compute_diversity_entropy()
        logger.info("📊 DH=%.3f (passed=%d)", dh, len(guard._passed_ics))
    # Log exploration diversity stats
    explore = engine.get_exploration_stats()
    if explore:
        top5_explore = list(explore.items())[:5]
        logger.info("Explore top-5: %s",
                    ", ".join(f"{k}={v}" for k, v in top5_explore))
    # Log field distribution of passed factors this round
    field_counts: dict[str, int] = {}
    for e in kb.entries[-stats["evaluated"]:]:
        if e.passed:
            for m in re.finditer(r'\$(\w+)', e.expression):
                f = m.group(1)
                cat = ("price" if f in PRICE_FIELDS
                       else "vol" if f in VOLUME_FIELDS
                       else "fund")
                field_counts[cat] = field_counts.get(cat, 0) + 1
    if field_counts:
        logger.info("Field dist passed: %s", field_counts)
    # Log rejected gems (high-ICIR factors killed by ic_corr)
    if guard is not None and guard.rejected_gems:
        recent = [g for g in guard.rejected_gems[-5:]]
        if recent:
            logger.info("💎 Rejected gems (%d total): %s",
                        len(guard.rejected_gems),
                        "; ".join(f"ICIR={g['icir']:.2f}→{g['corr_with']}"
                                  for g in recent))
    # P0-1: Log conditional replacements
    if guard is not None and guard.replacements:
        logger.info("🔄 Replacements (%d total): %s",
                    len(guard.replacements),
                    "; ".join(
                        f"{r['old_name']}→{r['new_name']}"
                        f"(ICIR {r['old_icir']:.2f}→{r['new_icir']:.2f})"
                        for r in guard.replacements[-3:]))
    return stats


def run_daemon(args: argparse.Namespace) -> None:
    """守护进程主循环."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    kb_path = output_dir / "mutation_kb.json"
    kb = PersistentKnowledgeBase(kb_path)
    # ── 初始化 DB 存储 ──
    try:
        from aipha.factors.mining_store import MiningStore
        store = MiningStore()
        logger.info("MiningStore 初始化成功: %s", store.db_path)
    except Exception as exc:
        logger.warning("MiningStore 初始化失败 (%s), 继续无 DB 模式", exc)
        store = None
    # ── P2-3: 解析多周期前向收益 ──
    fwd_periods = [int(x) for x in str(args.fwd).split(",")]
    primary_fwd = fwd_periods[0]
    fwd_col = f"fwd_{primary_fwd}d"
    extra_fwd_cols = [f"fwd_{p}d" for p in fwd_periods[1:]]
    logger.info("加载市场数据 (仅一次)… fwd_periods=%s", fwd_periods)
    df = load_market_data(args.universe, args.start, args.end,
                          primary_fwd, fwd_periods=fwd_periods)
    seed_file = Path(args.seed)
    if not seed_file.exists():
        logger.error("种子文件不存在: %s", seed_file)
        return
    seeds = json.loads(seed_file.read_text("utf-8"))
    # P0-2: 注入基本面种子 — 打破 $close 主导的种子偏差 (v5 $close=83%)
    FUNDAMENTAL_SEEDS = [
        # ── 原始基本面种子 ──
        {"name": "seed_low_pb", "expression": "cs_rank(-$pb)"},
        {"name": "seed_low_pe", "expression": "cs_rank(-$pe_ttm)"},
        {"name": "seed_high_div", "expression": "cs_rank($dv_ttm)"},
        {"name": "seed_small_cap", "expression": "cs_rank(-$total_mv)"},
        {"name": "seed_low_vol",
         "expression": "cs_rank(-ts_std($close, 20))"},
        {"name": "seed_turnover",
         "expression": "cs_rank(ts_mean($turnover_rate, 20))"},
        {"name": "seed_momentum5",
         "expression": "ts_delta($close, 5) / $close"},
        {"name": "seed_low_range",
         "expression": "cs_rank(-($high - $low) / $close)"},
        # ── P2-2: $open 与隔夜收益种子 ──
        {"name": "seed_overnight_gap",
         "expression": "cs_rank(safe_div($open - ts_delay($close, 1), ts_delay($close, 1) + 1e-8))"},
        {"name": "seed_open_close_ratio",
         "expression": "cs_rank(safe_div($open, $close + 1e-8))"},
        {"name": "seed_open_revert",
         "expression": "cs_rank(-ts_mean(safe_div($open - ts_delay($close, 1), ts_delay($close, 1) + 1e-8), 20))"},
        # ── P2-2: 量价交互种子 ──
        {"name": "seed_vwap_dev",
         "expression": "cs_rank(safe_div($amount, $volume + 1e-8) - $close)"},
        {"name": "seed_money_flow",
         "expression": "cs_rank(ts_mean($amount, 5) - ts_mean($amount, 20))"},
        # ── P2-2: 深度基本面组合种子 ──
        {"name": "seed_div_per_mv",
         "expression": "cs_rank(safe_div($dv_ttm, $total_mv + 1e-8))"},
        {"name": "seed_ep_momentum",
         "expression": "cs_rank(ts_delta(safe_div(1, $pe_ttm + 1e-8), 20))"},
        {"name": "seed_pb_vol_adj",
         "expression": "cs_rank(-safe_div($pb, ts_std($close, 20) + 1e-8))"},
        # ── P2-2: 技术微观结构种子 ──
        {"name": "seed_avg_range",
         "expression": "cs_rank(ts_mean(safe_div($high - $low, $open + 1e-8), 10))"},
        {"name": "seed_close_position",
         "expression": "cs_rank(ts_mean(safe_div($close - $low, $high - $low + 1e-8), 10))"},
        # ── P3-2: R124 学术因子种子 (AlphaAgent/RD-Agent 文献) ──
        # GP/TA: Genetic Programming Technical Analysis (Becker 2021)
        {"name": "seed_gpta_range_ratio",
         "expression": "cs_rank(safe_div(ts_max($high, 20) - ts_min($low, 20), ts_mean($close, 20) + 1e-8))"},
        # 3M-Reversal: Jegadeesh (1990) short-term reversal
        {"name": "seed_3m_reversal",
         "expression": "cs_rank(-ts_delta($close, 60) / ($close + 1e-8))"},
        # Value+Profitability interaction (Novy-Marx 2013)
        {"name": "seed_value_profit",
         "expression": "cs_rank(safe_div($dv_ttm, $pb + 1e-8))"},
        # Amihud illiquidity (Amihud 2002)
        {"name": "seed_amihud_illiq",
         "expression": "cs_rank(ts_mean(safe_div(ts_abs(ts_delta($close, 1)), $amount + 1e-8), 20))"},
        # Earnings momentum / fundamental drift
        {"name": "seed_ep_drift",
         "expression": "cs_rank(ts_slope(safe_div(1, $pe_ttm + 1e-8), 60))"},
        # Volume-price divergence (Pastor & Stambaugh 2003)
        {"name": "seed_vol_price_div",
         "expression": "cs_rank(-ts_corr($close, $volume, 20))"},
    ]
    existing_exprs = {s["expression"] for s in seeds}
    added_fund = 0
    for fs in FUNDAMENTAL_SEEDS:
        if fs["expression"] not in existing_exprs:
            seeds.append(fs)
            added_fund += 1
    logger.info("注入 %d/%d 个基本面种子 (总种子=%d)",
                added_fund, len(FUNDAMENTAL_SEEDS), len(seeds))
    logger.info("加载 %d 个种子因子", len(seeds))
    rng = random.Random(42)
    engine = MutationEngine(seeds, rng)
    batch_size = args.batch
    from aipha.factors.factor_expression_parser import FactorExpressionParser
    parser = FactorExpressionParser()
    # ── 初始化 Guard Pipeline ──
    guard = FactorGuardPipeline(
        ast_sim_threshold=0.85,
        ic_corr_threshold=0.70,
        hlz_t_threshold=HLZ_T_THRESHOLD,
    )
    # 注：不将种子注册到 AST 去重器 — 种子是变异起点，不是目标池
    # Guard 仅在新通过因子间进行去重，避免同质化累积
    logger.info("Guard Pipeline 初始化完成 (seeds=%d)", len(seeds))
    state = DaemonState()
    # 单轮模式
    if args.once:
        result = run_mining_round(
            1, df, fwd_col, engine, kb, parser, guard, batch_size,
            extra_fwd_cols=extra_fwd_cols)
        state.total_rounds = 1
        state.total_candidates = result["generated"]
        state.total_passed = result["passed"]
        _print_summary(state, kb)
        return
    # 持续挖掘
    logger.info("启动持续挖掘, 间隔=%d分钟", args.interval)
    stop = threading.Event()
    def _signal_handler(sig, frame):
        logger.info("收到停止信号, 优雅退出…")
        stop.set()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    while not stop.is_set():
        state.total_rounds += 1
        rid = state.total_rounds
        result = run_mining_round(
            rid, df, fwd_col, engine, kb, parser, guard, batch_size,
            extra_fwd_cols=extra_fwd_cols)
        state.total_candidates += result["generated"]
        state.total_passed += result["passed"]
        state.last_round_at = datetime.now().isoformat()
        # ── 持久化到 DB ──
        if store is not None:
            _persist_round_to_db(store, kb, rid, result, str(kb_path))
        # ── 自动评估触发 ──
        eval_interval = getattr(args, "eval_interval", 0)
        if (eval_interval > 0
                and rid % eval_interval == 0
                and len(kb.get_passed()) >= 5):
            _trigger_auto_evaluation(
                kb_path, df, args, store=store)
        if stop.wait(timeout=args.interval * 60):
            break
    _print_summary(state, kb)


def _persist_round_to_db(
    store: Any, kb: PersistentKnowledgeBase,
    round_id: int, stats: dict, kb_path_str: str,
) -> None:
    """将当次挖掘轮次的因子和汇总写入 DB."""
    try:
        n_eval = stats.get("evaluated", 0)
        recent_entries = kb.entries[-n_eval:] if n_eval > 0 else []
        batch = []
        for e in recent_entries:
            batch.append({
                "factor_id": f"R{e.round_id}_{e.name}",
                "name": e.name,
                "expression": e.expression,
                "category": e.category,
                "method": e.method,
                "ic_mean": e.ic_mean,
                "icir": e.icir,
                "post_ic": e.post_ic,
                "fitness": e.fitness,
                "passed": e.passed,
                "failure_reason": e.failure_reason,
                "round_id": e.round_id,
                "kb_path": kb_path_str,
                "discovered_at": e.discovered_at,
            })
        if batch:
            store.upsert_mined_factors_batch(batch)
        dh = 0.0
        store.upsert_mining_round({
            "round_id": round_id,
            "kb_path": kb_path_str,
            "generated": stats.get("generated", 0),
            "passed": stats.get("passed", 0),
            "failed": stats.get("evaluated", 0) - stats.get("passed", 0),
            "dh_score": dh,
            "elapsed_s": stats.get("elapsed_sec", 0),
        })
    except Exception as exc:
        logger.warning("DB 持久化失败 (R%d): %s", round_id, exc)


def _trigger_auto_evaluation(
    kb_path: Path, df: pd.DataFrame,
    args: argparse.Namespace,
    store: Any = None,
) -> None:
    """自动触发因子评估管道 (skip-wf 加速)."""
    try:
        from factor_evaluation_pipeline import run_evaluation
        logger.info(">>> 自动评估触发 (KB=%s)", kb_path)
        result = run_evaluation(
            str(kb_path), df=df, skip_wf=True,
            output_dir=str(kb_path.parent),
            store=store,
        )
        n_ind = result.get("n_independent", 0)
        n_passed = result.get("n_passed", 0)
        logger.info("<<< 自动评估完成: %d/%d 独立因子", n_ind, n_passed)
    except Exception as e:
        logger.warning("自动评估失败: %s", e)


def _print_summary(state: DaemonState, kb: PersistentKnowledgeBase) -> None:
    """打印挖掘汇总."""
    passed = kb.get_passed()
    logger.info("=" * 60)
    logger.info("挖掘汇总: %d轮, %d候选, %d通过 (%.1f%%)",
                state.total_rounds, state.total_candidates,
                len(passed),
                100 * len(passed) / max(state.total_candidates, 1))
    for f in passed:
        logger.info("  ✓ %s  ICIR=%.3f  expr=%s",
                     f.name, f.icir, f.expression[:60])
    logger.info("=" * 60)


def parse_args() -> argparse.Namespace:
    """解析命令行参数."""
    p = argparse.ArgumentParser(description="24/7 因子变异挖掘守护进程")
    p.add_argument("--once", action="store_true", help="仅运行一轮")
    p.add_argument("--daemon", action="store_true", help="后台守护模式")
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MIN,
                   help=f"挖掘间隔(分钟), 默认{DEFAULT_INTERVAL_MIN}")
    p.add_argument("--batch", type=int, default=CANDIDATES_PER_ROUND,
                   help=f"每轮候选数, 默认{CANDIDATES_PER_ROUND}")
    p.add_argument("--universe", default="csi2000", help="股票池")
    p.add_argument("--start", default="20150101", help="数据起始日")
    p.add_argument("--end", default="20260301", help="数据截止日")
    p.add_argument("--fwd", type=str, default="10",
                   help="前向收益天数, 逗号分隔支持多周期 (如 5,10,20)")
    p.add_argument("--seed", default="output/auto_mining/passed_factors.json",
                   help="种子因子文件")
    p.add_argument("--output", default="output/auto_mining",
                   help="输出目录")
    p.add_argument("--eval-interval", type=int, default=10,
                   dest="eval_interval",
                   help="每 N 轮自动触发因子评估 (0=禁用, 默认10)")
    return p.parse_args()


def main() -> None:
    """入口."""
    args = parse_args()
    # 日志设置 — 使用 force=True 覆盖模块级 basicConfig
    log_fmt = "%(asctime)s [%(levelname)s] %(message)s"
    log_file = Path(args.output) / "daemon.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(log_fmt))
    # 添加到根 logger 和 daemon logger
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    logger.addHandler(file_handler)
    logger.info("因子挖掘守护进程启动 (interval=%dmin, batch=%d)",
                args.interval, args.batch)
    run_daemon(args)


if __name__ == "__main__":
    main()
