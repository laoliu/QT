"""Scorer 注册表与三类打分实现。

重点守两件事：
1. **名字解析**。两条调用路径传进来的名字不同（orchestrator 传规范名，
   component_rebalance/live_engine 传组件名），别名表对不上就会选不出股。
2. **失败必须显式**。未注册的名字、缺模型、特征对不上，都要 raise 而不是
   返回空列表 —— 上层把空选股当作「今日无合格标的」直接跳过调仓。
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from aipha.runner import scorers
from aipha.runner.scorers import (
    MIN_FEATURE_COVERAGE,
    available_scorers,
    get_scorer,
    register_scorer,
    resolve_scorer_name,
    score_cb_stock_quality,
    score_etf_faa,
    score_v2_ml_ensemble,
)


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------

class _FakeGw:
    """只回应 cb_basic 的最小 gateway。"""

    def __init__(self, basic: pd.DataFrame | None = None) -> None:
        self.basic = basic if basic is not None else _cb_basic()
        self.calls: list[str] = []

    def call(self, endpoint: str, **kwargs: object) -> pd.DataFrame:
        self.calls.append(endpoint)
        if endpoint == "cb_basic":
            return self.basic
        return pd.DataFrame()


def _cb_basic(**overrides: object) -> pd.DataFrame:
    rows = {
        "ts_code": ["113001.SH", "113002.SH", "113003.SH"],
        "stk_code": ["600000.SH", "600001.SH", "600002.SH"],
        # 单位为元 —— scorer 需换算成万元
        "issue_size": [1e8, 2e8, 3e8],
        "list_date": ["20200101", "20200101", "20200101"],
        "maturity_date": ["20300101", "20300101", "20300101"],
    }
    rows.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(rows)


def _cb_daily(**overrides: object) -> pd.DataFrame:
    rows = {
        "ts_code": ["113001.SH", "113002.SH", "113003.SH"],
        "close": [105.0, 120.0, 140.0],
        "cb_over_rate": [5.0, 20.0, 40.0],
        "amount": [500.0, 400.0, 300.0],
        "vol": [50000.0, 40000.0, 30000.0],
    }
    rows.update(overrides)  # type: ignore[arg-type]
    return pd.DataFrame(rows)


def _etf_rows(
    start: float = 1.0,
    step: float = 0.01,
    n: int = 200,
    amount: float = 5000.0,
) -> list[tuple[str, float, float]]:
    """构造单调价格序列 —— step 越大动量越强。"""
    return [
        (f"2025{(i % 12) + 1:02d}{(i % 28) + 1:02d}", start * (1 + step) ** i, amount)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_canonical_names_registered(self) -> None:
        assert available_scorers() == [
            "cb_stock_quality", "etf_faa", "ml_csi1000",
            "ml_csi2000", "ml_stocks", "v2_ml_ensemble",
        ]

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("ml", "score_ml_csi2000"),
            ("cb", "score_cb_stock_quality"),
            ("etf", "score_etf_faa"),
            ("ml_csi2000", "score_ml_csi2000"),
            ("cb_stock_quality", "score_cb_stock_quality"),
            ("etf_faa", "score_etf_faa"),
        ],
    )
    def test_component_names_resolve(self, name: str, expected: str) -> None:
        """组件名走别名表 —— configs/ 里没有 scorer: 键，回落的就是组件名。"""
        assert get_scorer(name).__name__ == expected

    def test_whitespace_tolerated(self) -> None:
        assert get_scorer("  cb  ").__name__ == "score_cb_stock_quality"

    def test_unknown_name_raises_and_lists_options(self) -> None:
        """未注册必须 raise：返回空选股会被上层当成「今日无标的」静默跳过。"""
        with pytest.raises(KeyError) as exc:
            get_scorer("ml_csi9999")
        message = str(exc.value)
        assert "ml_csi9999" in message
        assert "cb_stock_quality" in message  # 错误信息里要给出可用名

    def test_duplicate_registration_rejected(self) -> None:
        with pytest.raises(ValueError, match="已注册"):
            register_scorer("etf_faa", lambda gw, cfg, td: [])

    def test_override_allowed_explicitly(self) -> None:
        original = get_scorer("etf_faa")
        try:
            register_scorer("etf_faa", lambda gw, cfg, td: ["X"], override=True)
            assert get_scorer("etf_faa")(None, {}, "20250106") == ["X"]
        finally:
            register_scorer("etf_faa", original, override=True)

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            register_scorer("  ", lambda gw, cfg, td: [])

    def test_resolve_prefers_explicit_scorer_key(self) -> None:
        assert resolve_scorer_name("ml", {"scorer": "etf_faa"}) == "etf_faa"

    def test_resolve_falls_back_to_component_name(self) -> None:
        assert resolve_scorer_name("cb", {}) == "cb_stock_quality"

    def test_resolve_passes_unknown_through(self) -> None:
        """解析不改名字，只做别名替换 —— 未知名字留给 get_scorer 报错。"""
        assert resolve_scorer_name("whatever", {}) == "whatever"


class TestOrchestratorNamesMatch:
    """ComboOrchestrator._infer_scorer_name 推出来的名字必须都能拿到函数。

    两处各有一份别名表，漂移了就会在盘中才发现。
    """

    @pytest.mark.parametrize(
        ("comp_name", "comp_cfg"),
        [
            ("ml", {}),
            ("cb", {}),
            ("etf", {}),
            ("ml_csi1000", {}),
            ("ml_csi2000", {}),
            ("anything", {"model_dirs": ["models/a"]}),
            ("anything", {"strategy_name": "cb_stock_quality"}),
            ("anything", {"strategy_name": "etf_cross_v6"}),
            ("equity", {"asset_type": "a_stock", "universe": "csi1000"}),
            ("equity", {"asset_type": "a_stock", "universe": "csi2000"}),
            ("equity", {"asset_type": "cn_equity", "universe": "hs300"}),
            ("bond", {"asset_type": "convertible_bond"}),
            ("fund", {"asset_type": "etf"}),
        ],
    )
    def test_inferred_name_is_registered(
        self, comp_name: str, comp_cfg: dict,
    ) -> None:
        from aipha.runner.combo_orchestrator import ComboOrchestrator

        name = ComboOrchestrator._infer_scorer_name(comp_name, comp_cfg)
        assert name is not None, "推导应给出名字"
        assert get_scorer(name) is not None


# ---------------------------------------------------------------------------
# CB
# ---------------------------------------------------------------------------

def _run_cb(
    comp_cfg: dict,
    daily: pd.DataFrame | None = None,
    basic: pd.DataFrame | None = None,
    *,
    stub_stock_quality: bool = True,
) -> list[str]:
    gw = _FakeGw(basic)
    daily_df = daily if daily is not None else _cb_daily()
    ctx = patch(
        "aipha.data.cb_data_enricher.fetch_cb_daily_enriched",
        return_value=daily_df,
    )
    if stub_stock_quality:
        # 正股因子单独测；这里只关心行情/静态字段这条链路
        quality = patch.object(
            scorers, "_attach_stock_quality", side_effect=lambda df, cfg, td: df,
        )
    else:
        quality = patch.object(scorers, "_attach_stock_quality", wraps=scorers._attach_stock_quality)
    with ctx, quality:
        return score_cb_stock_quality(gw, comp_cfg, "20250106")


class TestCBScorer:
    def test_ranks_by_weighted_factors(self) -> None:
        """只给价格权重时，最低价的债排第一。"""
        picks = _run_cb({"top_k": 1, "factor_weights": {"price": 1.0}})

        assert picks == ["113001.SH"]

    def test_top_k_respected(self) -> None:
        picks = _run_cb({"top_k": 2, "factor_weights": {"price": 1.0}})

        assert len(picks) == 2

    def test_price_filter_applied(self) -> None:
        picks = _run_cb({"top_k": 10, "max_price": 110.0})

        assert picks == ["113001.SH"]

    def test_premium_filter_applied(self) -> None:
        picks = _run_cb({"top_k": 10, "max_premium": 10.0})

        assert picks == ["113001.SH"]

    def test_turnover_filter_applied(self) -> None:
        """min_turnover 与 cb_daily.amount 同为万元，直接比较。"""
        picks = _run_cb({"top_k": 10, "min_turnover": 450.0})

        assert picks == ["113001.SH"]

    def test_issue_size_cap_converted_to_wan(self) -> None:
        """cb_basic.issue_size 单位元；2 亿 = 20000 万元应被 15000 上限剔除。"""
        picks = _run_cb({"top_k": 10, "max_issue_size": 15000.0})

        assert picks == ["113001.SH"]

    def test_age_filter_excludes_new_bonds(self) -> None:
        basic = _cb_basic(list_date=["20250105", "20200101", "20200101"])

        picks = _run_cb({"top_k": 10, "min_age_days": 3}, basic=basic)

        assert "113001.SH" not in picks

    def test_unparseable_list_date_kept(self) -> None:
        """上市日缺失不等于次新 —— 不能因为字段脏就把标的丢掉。"""
        basic = _cb_basic(list_date=["", None, "20200101"])

        picks = _run_cb({"top_k": 10, "min_age_days": 3}, basic=basic)

        assert len(picks) == 3

    def test_maturity_filter_excludes_near_maturity(self) -> None:
        """到期过滤必须由 scorer 做：rank_cb_quality 只看价格/溢价/成交额。"""
        basic = _cb_basic(maturity_date=["20250110", "20300101", "20300101"])

        picks = _run_cb({"top_k": 10, "min_days_to_maturity": 30}, basic=basic)

        assert "113001.SH" not in picks
        assert len(picks) == 2

    def test_empty_daily_returns_empty(self) -> None:
        picks = _run_cb({"top_k": 10}, daily=pd.DataFrame())

        assert picks == []

    def test_empty_cb_basic_raises(self) -> None:
        """缺静态字段就无法做强赎风险过滤 —— 宁可当日不调仓。"""
        with pytest.raises(RuntimeError, match="cb_basic"):
            _run_cb({"top_k": 10}, basic=pd.DataFrame())

    def test_bad_factor_weights_type_raises(self) -> None:
        with pytest.raises(ValueError, match="factor_weights"):
            _run_cb({"top_k": 10, "factor_weights": [0.5, 0.5]})

    def test_non_positive_top_k_raises(self) -> None:
        with pytest.raises(ValueError, match="top_k"):
            _run_cb({"top_k": 0})

    def test_r115_config_shape_accepted(self) -> None:
        """按 configs/paper/r115_combo.yml 的 cb 段原样传入。"""
        cfg = {
            "asset_type": "convertible_bond",
            "top_k": 2,
            "max_price": 145.0,
            "max_issue_size": 20000.0,
            "min_turnover": 20.0,
            "max_premium": 60.0,
            "min_age_days": 3,
            "min_days_to_maturity": 30,
            "factor_weights": {
                "price": 0.18, "premium": 0.12, "stock_roe": 0.25,
                "stock_ep": 0.20, "stock_vol": 0.15, "issue_size": 0.10,
            },
        }

        picks = _run_cb(cfg)

        assert len(picks) == 2


class TestCBStockQuality:
    """正股因子附加 —— 桥接不可用时必须退化而不是崩。"""

    def test_missing_stk_code_defaults_to_zero(self) -> None:
        df = _cb_daily().drop(columns=[])
        out = scorers._attach_stock_quality(df, {}, "20250106")

        assert (out["roe"] == 0.0).all()
        assert (out["ep"] == 0.0).all()
        assert (out["vol"] == 0.0).all()

    def test_cb_volume_never_leaks_into_stock_vol(self) -> None:
        """cb_daily.vol 是转债成交量（张），不是正股波动率。

        rank_cb_quality 把 stock_vol 权重映射到 vol 列并按升序打分，沿用
        原列会变成「按转债成交量升序」—— 用 0.15 的权重偏好最不活跃的债。
        """
        df = _cb_daily()  # vol = 3~5 万张
        out = scorers._attach_stock_quality(df, {}, "20250106")

        assert (out["vol"] == 0.0).all()
        assert out["cb_vol"].tolist() == [50000.0, 40000.0, 30000.0]

    def test_cb_volume_preserved_under_bridge_failure(self) -> None:
        df = _cb_daily()
        df["stk_code"] = ["600000.SH", "600001.SH", "600002.SH"]

        with patch(
            "aipha.data.cb_stock_bridge.CBStockBridge",
            side_effect=RuntimeError("no token"),
        ):
            out = scorers._attach_stock_quality(df, {}, "20250106")

        assert (out["vol"] == 0.0).all()
        assert "cb_vol" in out.columns

    def test_bridge_failure_degrades_to_cb_only(self) -> None:
        df = _cb_daily()
        df["stk_code"] = ["600000.SH", "600001.SH", "600002.SH"]

        with patch(
            "aipha.data.cb_stock_bridge.CBStockBridge",
            side_effect=RuntimeError("no token"),
        ):
            out = scorers._attach_stock_quality(df, {}, "20250106")

        assert (out["roe"] == 0.0).all()
        assert len(out) == 3  # 标的没被丢掉，只是正股因子中性


# ---------------------------------------------------------------------------
# ETF
# ---------------------------------------------------------------------------

def _run_etf(comp_cfg: dict, history: dict) -> list[str]:
    with patch.object(scorers, "_load_etf_history", return_value=history):
        return score_etf_faa(None, comp_cfg, "20250106")


class TestETFScorer:
    def test_strongest_momentum_wins(self) -> None:
        history = {
            "A.SH": _etf_rows(step=0.001),
            "B.SH": _etf_rows(step=0.005),
        }

        picks = _run_etf(
            {
                "symbols": ["A.SH", "B.SH"], "top_k": 1,
                "weight_momentum": 1.0, "weight_volatility": 0.0,
                "weight_correlation": 0.0, "weight_volume": 0.0,
            },
            history,
        )

        assert picks == ["B.SH"]

    def test_top_k_truncates(self) -> None:
        history = {
            f"{i}.SH": _etf_rows(step=0.001 * (i + 1)) for i in range(5)
        }

        picks = _run_etf(
            {"symbols": list(history), "top_k": 2}, history,
        )

        assert len(picks) == 2

    def test_trend_filter_excludes_below_ma(self) -> None:
        """跌破均线不参与 —— 与回测策略的趋势过滤同口径。"""
        rising = _etf_rows(step=0.005)
        falling = _etf_rows(step=-0.005)
        history = {"UP.SH": rising, "DOWN.SH": falling}

        picks = _run_etf(
            {"symbols": ["UP.SH", "DOWN.SH"], "top_k": 5,
             "trend_ma_period": 120},
            history,
        )

        assert picks == ["UP.SH"]

    def test_min_amount_filter(self) -> None:
        history = {
            "THIN.SH": _etf_rows(step=0.005, amount=10.0),
            "THICK.SH": _etf_rows(step=0.001, amount=9000.0),
        }

        picks = _run_etf(
            {"symbols": ["THIN.SH", "THICK.SH"], "top_k": 5,
             "min_amount": 1000.0},
            history,
        )

        assert picks == ["THICK.SH"]

    def test_short_history_skipped(self) -> None:
        history = {
            "SHORT.SH": _etf_rows(n=5),
            "OK.SH": _etf_rows(n=200),
        }

        picks = _run_etf({"symbols": ["SHORT.SH", "OK.SH"], "top_k": 5}, history)

        assert picks == ["OK.SH"]

    def test_missing_symbols_raises(self) -> None:
        """ETF 池不存在合理默认值 —— 不能悄悄换成全市场。"""
        with pytest.raises(ValueError, match="symbols"):
            score_etf_faa(None, {"top_k": 3}, "20250106")

    def test_no_history_raises(self) -> None:
        with pytest.raises(RuntimeError, match="未取到"):
            _run_etf({"symbols": ["A.SH"], "top_k": 1}, {})

    def test_all_filtered_returns_empty(self) -> None:
        """有数据但全被过滤 → 空列表（确实无合格标的），不 raise。"""
        history = {"DOWN.SH": _etf_rows(step=-0.005)}

        picks = _run_etf(
            {"symbols": ["DOWN.SH"], "top_k": 1, "trend_ma_period": 120},
            history,
        )

        assert picks == []

    def test_ties_broken_deterministically(self) -> None:
        """同一份数据必须给出同一份选股，否则事后无法复现当日决策。"""
        history = {
            "B.SH": _etf_rows(step=0.003),
            "A.SH": _etf_rows(step=0.003),
        }
        cfg = {"symbols": ["B.SH", "A.SH"], "top_k": 1}

        assert _run_etf(cfg, history) == _run_etf(cfg, history) == ["A.SH"]

    def test_non_positive_top_k_raises(self) -> None:
        with pytest.raises(ValueError, match="top_k"):
            score_etf_faa(None, {"symbols": ["A.SH"], "top_k": -1}, "20250106")

    def test_correlation_hard_exclude(self) -> None:
        """与核心策略高相关的 ETF 被硬排除（Keller FAA 的 C 因子）。"""
        history = {
            "SAME.SH": _etf_rows(step=0.005),
            "DIFF.SH": _etf_rows(step=0.004),
        }
        # SAME 与基准完全同向，DIFF 用不上基准（日期对不上）
        bench = {row[0]: 0.005 for row in history["SAME.SH"]}

        with patch.object(scorers, "_load_benchmark_returns", return_value=bench), \
             patch.object(
                 scorers, "_corr_with_benchmark",
                 side_effect=lambda rows, bm, lb: 0.9 if rows is history["SAME.SH"] else 0.1,
             ):
            picks = _run_etf(
                {"symbols": ["SAME.SH", "DIFF.SH"], "top_k": 5,
                 "benchmark_returns_path": "x.csv",
                 "correlation_hard_exclude": 0.6},
                history,
            )

        assert picks == ["DIFF.SH"]


class TestFAAHelpers:
    def test_rank_normalize_spans_zero_to_one(self) -> None:
        assert scorers._rank_normalize([1.0, 2.0, 3.0]) == [0.0, 0.5, 1.0]

    def test_rank_normalize_averages_ties(self) -> None:
        assert scorers._rank_normalize([5.0, 5.0]) == [0.5, 0.5]

    def test_rank_normalize_single_value(self) -> None:
        assert scorers._rank_normalize([7.0]) == [0.5]
        assert scorers._rank_normalize([]) == []

    def test_realized_vol_zero_for_flat_series(self) -> None:
        assert scorers._realized_vol([10.0] * 30, 20) == 0.0

    def test_realized_vol_positive_for_moving_series(self) -> None:
        closes = [10.0 + (i % 2) for i in range(30)]
        assert scorers._realized_vol(closes, 20) > 0

    def test_realized_vol_insufficient_history(self) -> None:
        assert scorers._realized_vol([1.0, 2.0], 20) == 0.0

    def test_pearson_perfect_correlation(self) -> None:
        assert scorers._pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)

    def test_pearson_zero_variance_returns_none(self) -> None:
        assert scorers._pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None

    def test_pearson_length_mismatch_returns_none(self) -> None:
        assert scorers._pearson([1.0, 2.0], [1.0]) is None

    def test_corr_needs_enough_overlap(self) -> None:
        rows = _etf_rows(n=5)
        bench = {row[0]: 0.01 for row in rows}

        assert scorers._corr_with_benchmark(rows, bench, 60) is None

    def test_benchmark_missing_file_returns_empty(self) -> None:
        assert scorers._load_benchmark_returns("does/not/exist.csv") == {}

    def test_benchmark_empty_path_returns_empty(self) -> None:
        assert scorers._load_benchmark_returns("") == {}

    def test_benchmark_csv_parsed(self, tmp_path: Path) -> None:
        path = tmp_path / "bench.csv"
        path.write_text("date,return\n2025-01-06,0.01\n2025-01-07,-0.02\n")

        out = scorers._load_benchmark_returns(str(path))

        assert out == {"20250106": pytest.approx(0.01), "20250107": pytest.approx(-0.02)}

    def test_benchmark_csv_missing_columns(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.csv"
        path.write_text("a,b\n1,2\n")

        assert scorers._load_benchmark_returns(str(path)) == {}


# ---------------------------------------------------------------------------
# ML
# ---------------------------------------------------------------------------

class _FakeSelector:
    """固定分数的 MLStockSelector 替身。"""

    def __init__(self, scores: dict[str, float], features: list[str]) -> None:
        self._scores = scores
        self._features = features
        self._models: list[object] = []
        self._config = type("_Cfg", (), {"feature_cols": features})()

    def load(self, path: Path) -> None:
        self.loaded = path

    def predict(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(
            [self._scores.get(s, 0.0) for s in df["symbol"]],
            index=list(df["symbol"]),
        )


def _cross(symbols: list[str], features: list[str]) -> pd.DataFrame:
    data = {"symbol": symbols}
    for f in features:
        data[f] = [1.0] * len(symbols)
    return pd.DataFrame(data)


class TestModelDirs:
    def test_model_dir_without_meta_raises(self, tmp_path: Path) -> None:
        """模型没加载成功必须报错 —— 退化打分下单等于拿真钱试错。"""
        with pytest.raises(FileNotFoundError, match="meta.pkl"):
            scorers._model_dirs({"model_dir": str(tmp_path / "nope")})

    def test_single_model_dir_accepted(self, tmp_path: Path) -> None:
        (tmp_path / "meta.pkl").write_bytes(b"x")

        assert scorers._model_dirs({"model_dir": str(tmp_path)}) == [tmp_path]

    def test_model_dirs_plural_takes_precedence(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        a.mkdir()
        (a / "meta.pkl").write_bytes(b"x")

        dirs = scorers._model_dirs(
            {"model_dirs": [str(a)], "model_dir": str(tmp_path / "ignored")},
        )

        assert dirs == [a]

    def test_string_model_dirs_wrapped(self, tmp_path: Path) -> None:
        (tmp_path / "meta.pkl").write_bytes(b"x")

        assert scorers._model_dirs({"model_dirs": str(tmp_path)}) == [tmp_path]

    def test_no_config_gives_empty(self) -> None:
        assert scorers._model_dirs({}) == []


class TestFeatureCoverage:
    def test_full_coverage_passes(self) -> None:
        features = ["f1", "f2", "f3"]
        selector = _FakeSelector({}, features)

        scorers._check_feature_coverage(
            selector, _cross(["A"], features), Path("m"),
        )

    def test_low_coverage_raises(self) -> None:
        """特征缺失时 _resolve_features 会静默取交集 —— 这里把它变成硬错误。"""
        selector = _FakeSelector({}, [f"f{i}" for i in range(10)])

        with pytest.raises(RuntimeError, match="特征覆盖率"):
            scorers._check_feature_coverage(
                selector, _cross(["A"], ["f0", "f1"]), Path("m"),
            )

    def test_threshold_is_the_documented_one(self) -> None:
        n = 100
        features = [f"f{i}" for i in range(n)]
        keep = int(n * MIN_FEATURE_COVERAGE)
        selector = _FakeSelector({}, features)

        # 刚好达标不报错
        scorers._check_feature_coverage(
            selector, _cross(["A"], features[:keep]), Path("m"),
        )
        # 差一个就报错
        with pytest.raises(RuntimeError):
            scorers._check_feature_coverage(
                selector, _cross(["A"], features[: keep - 1]), Path("m"),
            )

    def test_no_expected_features_skips_check(self) -> None:
        selector = _FakeSelector({}, [])

        scorers._check_feature_coverage(selector, _cross(["A"], []), Path("m"))


class TestMLScorer:
    def test_legacy_feature_loader_is_not_an_implicit_fallback(self) -> None:
        with pytest.raises(RuntimeError, match="disabled by default"):
            scorers._load_ml_cross_section({}, "csi2000", "20250106")

    def _run(
        self,
        comp_cfg: dict,
        cross: pd.DataFrame,
        rank_by_dir: dict[str, dict[str, float]],
    ) -> list[str]:
        def fake_ranks(model_dir: Path, df: pd.DataFrame) -> pd.Series:
            return pd.Series(rank_by_dir[str(model_dir)])

        comp_cfg = {"legacy_ml_compat": True, **comp_cfg}

        with patch.object(scorers, "_load_ml_cross_section", return_value=cross), \
             patch.object(scorers, "_predict_ranks", side_effect=fake_ranks):
            return scorers._score_ml(
                None, comp_cfg, "20250106", default_universe="csi2000",
            )

    def test_picks_highest_ranked(self, tmp_path: Path) -> None:
        (tmp_path / "meta.pkl").write_bytes(b"x")
        cross = _cross(["A", "B", "C"], ["f1"])

        picks = self._run(
            {"model_dir": str(tmp_path), "top_k": 2},
            cross,
            {str(tmp_path): {"A": 0.2, "B": 0.9, "C": 0.5}},
        )

        assert picks == ["B", "C"]

    def test_ensemble_averages_ranks(self, tmp_path: Path) -> None:
        """rank_average：分数量纲不可比，集成必须在排名上做。"""
        a, b = tmp_path / "a", tmp_path / "b"
        for d in (a, b):
            d.mkdir()
            (d / "meta.pkl").write_bytes(b"x")
        cross = _cross(["A", "B"], ["f1"])

        picks = self._run(
            {"model_dirs": [str(a), str(b)], "top_k": 1},
            cross,
            {
                str(a): {"A": 1.0, "B": 0.0},
                str(b): {"A": 0.4, "B": 0.6},   # 均值 A=0.70 B=0.30
            },
        )

        assert picks == ["A"]

    def test_empty_cross_section_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "meta.pkl").write_bytes(b"x")

        picks = self._run(
            {"model_dir": str(tmp_path), "top_k": 5},
            pd.DataFrame(columns=["symbol", "f1"]),
            {},
        )

        assert picks == []

    def test_missing_model_config_raises(self) -> None:
        with pytest.raises(ValueError, match="model_dir"):
            scorers._score_ml(None, {"top_k": 5}, "20250106",
                              default_universe="csi2000")

    def test_non_positive_top_k_raises(self, tmp_path: Path) -> None:
        (tmp_path / "meta.pkl").write_bytes(b"x")

        with pytest.raises(ValueError, match="top_k"):
            scorers._score_ml(
                None, {"model_dir": str(tmp_path), "top_k": 0},
                "20250106", default_universe="csi2000",
            )

    def test_universe_default_used_when_absent(self, tmp_path: Path) -> None:
        (tmp_path / "meta.pkl").write_bytes(b"x")
        seen: dict[str, str] = {}

        def capture(cfg: dict, universe: str, trade_date: str) -> pd.DataFrame:
            seen["universe"] = universe
            return _cross(["A"], ["f1"])

        with patch.object(scorers, "_load_ml_cross_section", side_effect=capture), \
             patch.object(
                 scorers, "_predict_ranks",
                 return_value=pd.Series({"A": 1.0}),
             ):
            scorers._score_ml(
                None, {"model_dir": str(tmp_path)}, "20250106",
                default_universe="csi1000",
            )

        assert seen["universe"] == "csi1000"

    def test_config_universe_overrides_default(self, tmp_path: Path) -> None:
        (tmp_path / "meta.pkl").write_bytes(b"x")
        seen: dict[str, str] = {}

        def capture(cfg: dict, universe: str, trade_date: str) -> pd.DataFrame:
            seen["universe"] = universe
            return _cross(["A"], ["f1"])

        with patch.object(scorers, "_load_ml_cross_section", side_effect=capture), \
             patch.object(
                 scorers, "_predict_ranks",
                 return_value=pd.Series({"A": 1.0}),
             ):
            scorers._score_ml(
                None, {"model_dir": str(tmp_path), "universe": "micro"},
                "20250106", default_universe="csi2000",
            )

        assert seen["universe"] == "micro"

    def test_canonical_snapshot_is_pit_verified_and_filtered(self, tmp_path: Path) -> None:
        from aipha.research.snapshots import write_attested_parquet

        frame = pd.DataFrame({
            "trade_date": ["2025-01-06"] * 4,
            "symbol": ["A", "B", "C", "D"],
            "open": [10.0] * 4, "high": [10.0] * 4,
            "low": [10.0] * 4, "close": [10.0] * 4,
            "volume": [1000.0] * 4, "amount": [1000.0] * 4,
            "adj_factor": [1.0] * 4,
            "is_suspended": [False, True, False, False],
            "is_limit_up": [False] * 4, "is_limit_down": [False] * 4,
            "is_index_member": [True, True, False, True],
            "is_st": [False, False, True, False],
            "listing_days": [400, 400, 400, 100],
            "listing_days_basis": ["exchange_open_sessions"] * 4,
            "price_adjustment": ["point_in_time_factor"] * 4,
            "feature_version": ["cn_equity_v1"] * 4,
            "f1": [1.0, 2.0, 3.0, 4.0],
        })
        path = tmp_path / "snapshot.parquet"
        write_attested_parquet(
            frame, path,
            {
                "provider": "tushare_pit", "feature_version": "cn_equity_v1",
                "data_snapshot_id": "fixture", "start_date": "2025-01-06",
                "end_date": "2025-01-06", "index_code": "000905.SH",
            },
        )
        result = scorers._load_canonical_ml_cross_section(
            str(path), "csi500", "20250106", feature_version="cn_equity_v1",
        )
        assert result["symbol"].tolist() == ["A"]
        assert result.attrs["canonical_pit"] is True
        assert scorers._load_canonical_ml_cross_section(
            str(path), "csi500", "20250106", feature_version="cn_equity_v1", min_listing_days=401,
        ).empty
        frame["listing_days_basis"] = "calendar_days"
        write_attested_parquet(frame, path, {
            "provider": "tushare_pit", "feature_version": "cn_equity_v1",
            "data_snapshot_id": "legacy-age", "start_date": "2025-01-06",
            "end_date": "2025-01-06", "index_code": "000905.SH",
        })
        with pytest.raises(ValueError, match="rebuild legacy snapshots"):
            scorers._load_canonical_ml_cross_section(
                str(path), "csi500", "20250106", feature_version="cn_equity_v1",
            )


class TestV2Ensemble:
    def test_requires_model_dirs(self) -> None:
        with pytest.raises(ValueError, match="model_dirs"):
            score_v2_ml_ensemble(None, {"model_dir": "models/x"}, "20250106")

    def test_rejects_unimplemented_ensemble_method(self) -> None:
        """配置声明了别的集成方式却按 rank_average 跑 = 静默跑错策略。"""
        with pytest.raises(ValueError, match="rank_average"):
            score_v2_ml_ensemble(
                None,
                {"model_dirs": ["models/a"], "ensemble_method": "stacking"},
                "20250106",
            )


class TestMLFilters:
    def test_price_bounds(self) -> None:
        cross = pd.DataFrame({"symbol": ["A", "B", "C"], "close": [2.0, 50.0, 200.0]})

        out = scorers._apply_ml_filters(cross, {"min_price": 3.0, "max_price": 100.0})

        assert out["symbol"].tolist() == ["B"]

    def test_turnover_converted_from_thousand_to_wan(self) -> None:
        """TuShare daily.amount 单位千元，min_turnover 单位万元。"""
        cross = pd.DataFrame({"symbol": ["A", "B"], "amount": [1000.0, 5000.0]})

        out = scorers._apply_ml_filters(cross, {"min_turnover": 300.0})

        assert out["symbol"].tolist() == ["B"]

    def test_no_filters_passes_through(self) -> None:
        cross = pd.DataFrame({"symbol": ["A"], "close": [1.0]})

        assert len(scorers._apply_ml_filters(cross, {})) == 1


# ---------------------------------------------------------------------------
# 配置与日期工具
# ---------------------------------------------------------------------------

class TestConfigCoercion:
    def test_bad_number_falls_back_to_default(self) -> None:
        assert scorers._as_float({"x": "abc"}, "x", 1.5) == 1.5
        assert scorers._as_int({"x": "abc"}, "x", 7) == 7

    def test_none_falls_back_to_default(self) -> None:
        assert scorers._as_float({"x": None}, "x", 2.0) == 2.0
        assert scorers._as_bool({"x": None}, "x", True) is True

    def test_yaml_string_numbers_accepted(self) -> None:
        assert scorers._as_float({"x": "1.25"}, "x", 0.0) == 1.25
        assert scorers._as_int({"x": "3"}, "x", 0) == 3

    def test_bool_from_string(self) -> None:
        assert scorers._as_bool({"x": "true"}, "x", False) is True
        assert scorers._as_bool({"x": "no"}, "x", True) is False


class TestDateHelpers:
    def test_dashed_date_accepted(self) -> None:
        assert scorers._to_date("2025-01-06").isoformat() == "2025-01-06"

    def test_bad_date_raises(self) -> None:
        with pytest.raises(ValueError, match="YYYYMMDD"):
            scorers._to_date("not-a-date")

    def test_days_between_formats(self) -> None:
        from datetime import date as _date

        day = _date(2025, 1, 10)
        assert scorers._days_between("20250101", day) == 9
        assert scorers._days_between("2025-01-01", day) == 9

    def test_days_between_unparseable_is_nan(self) -> None:
        from datetime import date as _date

        assert math.isnan(scorers._days_between("", _date(2025, 1, 10)))
        assert math.isnan(scorers._days_between(None, _date(2025, 1, 10)))
        assert math.isnan(scorers._days_between("nan", _date(2025, 1, 10)))
