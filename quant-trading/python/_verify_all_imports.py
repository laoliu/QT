"""验证所有新增 aipha 模块可以正常导入"""
import sys
import importlib

modules = [
    # Phase 1 (prior session)
    "aipha.domain.models",
    "aipha.data.provider_base",
    "aipha.data.calendar",
    "aipha.data.cache_utils",
    "aipha.data.qt_adapter",
    "aipha.config.config_models",
    "aipha.config.config_parser",
    "aipha.factors.base",
    "aipha.factors.registry",
    "aipha.factors.core_factors",
    "aipha.strategy.base",
    "aipha.strategy.registry",
    "aipha.backtest.engine",
    "aipha.execution.fill_models",
    "aipha.execution.slippage",
    "aipha.execution.fees",
    "aipha.market.rules",
    "aipha.risk.constraints",
    "aipha.risk.var",
    "aipha.risk.audit_log",
    "aipha.models.feature_eng",
    "aipha.models.lgbm_ranker",
    "aipha.models.gru_ranker",
    "aipha.models.ensemble",
    "aipha.models.predictor",
    "aipha.portfolio.optimizer",
    "aipha.portfolio.regime_detector",
    "aipha.portfolio.strategy_combo",
    "aipha.validation.stats",
    # Phase 2 - Factor Expansion
    "aipha.factors.operators",
    "aipha.factors.alpha101",
    "aipha.factors.factor_analyzer",
    "aipha.factors.orthogonalization",
    "aipha.factors.composite",
    "aipha.factors.rolling_ic",
    # Phase 3 - ML Advanced
    "aipha.models.gat_ranker",
    "aipha.models.master_model",
    "aipha.models.double_ensemble",
    "aipha.models.feature_groups",
    "aipha.models.retrain_pipeline",
    "aipha.models.drift_monitor",
    "aipha.models.ml_stock_selector",
    # Phase 4 - Backtest Infra
    "aipha.backtest.result_store",
    "aipha.backtest.html_report",
    "aipha.backtest.data_loader",
    # Phase 5 - Risk Advanced
    "aipha.risk.stop_loss",
    "aipha.risk.degradation",
    "aipha.risk.euler_decomposition",
    "aipha.risk.ml_risk_controls",
    "aipha.risk.capacity_limit",
    "aipha.risk.position_sizing",
    # Phase 6 - Portfolio Advanced
    "aipha.portfolio.covariance",
    "aipha.portfolio.vol_targeting",
    "aipha.portfolio.compliance_engine",
    # Phase 7 - CB Suite
    "aipha.domain.convertible_bond",
    "aipha.data.cb_provider",
    "aipha.factors.cb_factors",
    "aipha.strategy.cb_strategies",
    # Phase 8 - Validation
    "aipha.validation.walk_forward",
]

sys.path.insert(0, ".")

ok = 0
fail = 0
for mod in modules:
    try:
        importlib.import_module(mod)
        print(f"  OK  {mod}")
        ok += 1
    except Exception as e:
        print(f" FAIL {mod} — {type(e).__name__}: {e}")
        fail += 1

print(f"\n{'='*50}")
print(f"Total: {len(modules)}  OK: {ok}  FAIL: {fail}")
