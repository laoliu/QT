"""Canonical A-share low-frequency research contracts and pipeline.

The rest of :mod:`aipha` contains legacy strategy and asset-specific modules.
New research code should depend on this package instead of importing a model or
provider directly from a command entry point.
"""

from .contracts import (
    FeatureFrame,
    FinancialSnapshot,
    MarketFrame,
    Prediction,
    ResearchRun,
    TargetPosition,
    visible_financial_snapshots,
    validate_feature_frame,
    validate_market_frame,
)
from .pipeline import (
    ResearchConfig,
    ResearchPipeline,
    build_forward_labels,
    build_target_positions,
)
from .artifacts import load_verified_selector
from .corporate_actions import CorporateAction, attach_corporate_actions, read_corporate_actions
from .config import (
    load_research_config,
    migrate_legacy_ml_config,
    migrate_legacy_ml_config_file,
)
from .execution_plan import (
    ExecutionPlan,
    build_execution_plan,
    execute_paper_target_plan,
    execute_target_plan,
    read_execution_plan,
    read_execution_plans,
    validate_execution_risk,
)
from .migration import migrate_legacy_model
from .paper_book import advance_paper_book, replay_paper_book, read_paper_book_evidence, upgrade_paper_book_calendar
from .snapshots import (
    SnapshotSpec,
    TushareSnapshot,
    assemble_tushare_snapshot,
    build_tushare_snapshot,
    build_cn_equity_features,
    financial_snapshots_from_provider,
    historical_st_status_from_provider,
    join_pit_financials,
    point_in_time_adjust_prices,
    frame_content_sha256,
    attest_snapshot_metadata,
    write_attested_parquet,
    verify_attested_parquet,
)

__all__ = [
    "FeatureFrame",
    "CorporateAction",
    "attach_corporate_actions",
    "read_corporate_actions",
    "FinancialSnapshot",
    "MarketFrame",
    "Prediction",
    "ResearchConfig",
    "ResearchPipeline",
    "ResearchRun",
    "TargetPosition",
    "visible_financial_snapshots",
    "build_forward_labels",
    "build_target_positions",
    "load_research_config",
    "migrate_legacy_ml_config",
    "migrate_legacy_ml_config_file",
    "load_verified_selector",
    "ExecutionPlan",
    "build_execution_plan",
    "execute_target_plan",
    "execute_paper_target_plan",
    "validate_execution_risk",
    "read_execution_plan",
    "read_execution_plans",
    "migrate_legacy_model",
    "advance_paper_book",
    "replay_paper_book",
    "read_paper_book_evidence",
    "upgrade_paper_book_calendar",
    "validate_feature_frame",
    "validate_market_frame",
    "SnapshotSpec",
    "TushareSnapshot",
    "assemble_tushare_snapshot",
    "build_tushare_snapshot",
    "build_cn_equity_features",
    "financial_snapshots_from_provider",
    "historical_st_status_from_provider",
    "join_pit_financials",
    "point_in_time_adjust_prices",
    "frame_content_sha256",
    "attest_snapshot_metadata",
    "write_attested_parquet",
    "verify_attested_parquet",
]
