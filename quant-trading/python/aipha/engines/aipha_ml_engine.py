"""Canonical A-share research bridge.

Actions: ``train`` (offline Parquet panel), ``predict``, ``evaluate``,
``export`` (target positions), ``train_tushare`` (materialize a frozen
TuShare PIT snapshot then train), and ``list_models``. Historical training
never falls back to AKShare.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent
_PYTHON_ROOT = _DIR.parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

DEFAULT_CONFIG = _PYTHON_ROOT / "aipha" / "configs" / "research" / "a_stock_ml.yaml"


def _resolve_runtime_path(value: str | os.PathLike[str] | None, *default: str) -> Path:
    from aipha.utils.paths import resolve_path

    return resolve_path(value, *default)


def _load_config(path: str | None) -> Any:
    from aipha.research.config import load_research_config

    return load_research_config(path or DEFAULT_CONFIG)


def _read_parquet(path: str | None):
    if not path:
        raise ValueError("dataPath is required")
    target = Path(path).expanduser().resolve()
    if not target.is_file() or target.suffix.lower() not in {".parquet", ".pq"}:
        raise ValueError(f"dataPath must point to an existing Parquet file: {target}")
    from aipha.research.snapshots import verify_attested_parquet

    import pandas as pd

    # Canonical PIT files must be content-attested.  Legacy/non-PIT fixtures
    # continue to require a sidecar but are not silently treated as TuShare
    # snapshots just because they carry a provider string.
    sidecar = target.with_suffix(".metadata.json")
    if not sidecar.is_file():
        raise ValueError(
            f"snapshot metadata sidecar required: {sidecar} "
            "(provider, data_snapshot_id, feature_version)"
        )
    raw_metadata = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(raw_metadata, dict):
        raise ValueError("snapshot metadata sidecar must contain a JSON object")
    metadata = verify_attested_parquet(
        target,
        require_artifact_hash=str(raw_metadata.get("provider", "")) == "tushare_pit",
        verify_content_hash=bool(raw_metadata.get("snapshot_content_sha256")),
        require_source_signature=str(raw_metadata.get("provider", "")) == "tushare_pit",
    )
    frame = pd.read_parquet(target)
    frame.attrs["snapshot_metadata"] = metadata
    return frame


def run(config: dict) -> dict:
    action = config.get("action", "list_models")
    try:
        if action == "list_models":
            return {"ok": True, "data": {"models": ["lgbm", "linear"], "default": "lgbm",
                                           "linear_implementation": "elasticnet"}}
        if action in {"train", "train_tushare"}:
            from aipha.research import ResearchPipeline

            cfg = _load_config(config.get("configPath"))
            model_type = str(config.get("modelType", "lgbm")).lower()
            if model_type not in {"lgbm", "linear"}:
                raise ValueError("modelType must be lgbm or linear")
            from dataclasses import replace
            cfg = replace(cfg, model_type="elasticnet" if model_type == "linear" else "lightgbm",
                          objective="regression" if model_type == "linear" else cfg.objective)
            overrides = config.get("modelParams") or {}
            if not isinstance(overrides, dict):
                raise ValueError("modelParams must be an object")
            allowed = {"n_estimators", "train_years", "test_months", "purge_days", "top_k"}
            unknown = set(overrides) - allowed
            if unknown:
                raise ValueError(f"unsupported modelParams: {', '.join(sorted(unknown))}")
            if overrides:
                cfg = replace(cfg, **{key: int(value) for key, value in overrides.items()})
            pipeline = ResearchPipeline(cfg)
            if action == "train_tushare":
                from aipha.data.tushare_equity_provider import get_equity_provider
                from aipha.data.tushare_pit_provider import TuSharePITProvider

                start_date = str(config.get("startDate", ""))
                end_date = str(config.get("endDate", ""))
                if not start_date or not end_date:
                    raise ValueError("startDate and endDate are required for train_tushare")
                equity_provider = get_equity_provider(
                    token=config.get("tushareToken"),
                    cache_dir=str(config.get("cacheDir", "data/cache/equity_tushare")),
                )
                result = pipeline.run_tushare(
                    equity_provider,
                    config.get("outputDir", "output/research/runs"),
                    start_date=start_date,
                    end_date=end_date,
                    index_code=str(config.get("indexCode", "000905.SH")),
                    benchmark_code=config.get("benchmarkCode"),
                    pit_provider=TuSharePITProvider(equity_provider),
                    use_cache=bool(config.get("useCache", True)),
                )
            else:
                result = pipeline.run(
                    _read_parquet(config.get("dataPath")),
                    config.get("outputDir", "output/research/runs"),
                    data_snapshot_id=config.get("dataSnapshotId"),
                )
            result["runEnvelope"] = result.pop("run_envelope")
            result["runId"] = result["run_id"]
            result["strategyId"] = result["strategy_id"]
            result["strategyVersion"] = result["strategy_version"]
            result["dataAsOf"] = result["data_as_of"]
            return {"ok": True, "data": result}
        if action == "predict":
            from aipha.research import load_verified_selector

            cfg = _load_config(config.get("configPath"))
            feature_path = config.get("featurePath") or config.get("dataPath")
            frame = _read_parquet(feature_path)
            from aipha.universe.historical import filter_historical_universe

            frame = filter_historical_universe(frame, min_listing_days=cfg.min_listing_days)
            if "datetime" not in frame and "trade_date" in frame:
                frame["datetime"] = frame["trade_date"]
            model_path = config.get("modelPath")
            if not model_path:
                raise ValueError("modelPath is required")
            selector = load_verified_selector(
                model_path,
                expected_feature_version=cfg.feature_version,
                expected_feature_columns=config.get("featureColumns"),
                expected_model_type="elasticnet" if str(config.get("modelType", "lgbm")).lower() == "linear" else "lightgbm",
            )
            frame = frame.copy()
            frame["score"] = selector.predict(frame).to_numpy() if len(frame) else []
            frame["trade_date"] = frame["datetime"]
            frame["rank"] = frame.groupby("trade_date")["score"].rank(
                ascending=False, method="first",
            ).astype(int)
            top_n = int(config.get("topN", cfg.top_k))
            result = frame.sort_values(["trade_date", "rank"]).groupby(
                "trade_date", sort=True,
            ).head(top_n)
            columns = ["trade_date", "symbol", "score", "rank"]
            result = result[columns]
            return {"ok": True, "data": {"rankings": result.to_dict("records"), "totalSymbols": int(len(frame)), "topN": top_n}}
        if action == "evaluate":
            from aipha.research.pipeline import _evaluate_predictions

            path = config.get("predictionsPath") or config.get("dataPath")
            frame = _read_parquet(path)
            from aipha.research import build_target_positions
            cfg = _load_config(config.get("configPath"))
            normalized = frame.rename(columns={"datetime": "trade_date", "pred_score": "score"})
            normalized["trade_date"] = __import__("pandas").to_datetime(normalized["trade_date"]).dt.date
            targets = build_target_positions(
                normalized,
                top_k=cfg.top_k,
                max_position_weight=cfg.max_position_weight,
                max_industry_weight=cfg.max_industry_weight,
            )
            metrics = _evaluate_predictions(normalized, targets, cfg)
            return {"ok": True, "data": {"metrics": metrics, "rows": int(len(normalized))}}
        if action == "export":
            from aipha.research import build_target_positions
            from aipha.research.execution_plan import build_execution_plan

            cfg = _load_config(config.get("configPath"))
            source = _read_parquet(config.get("predictionsPath") or config.get("dataPath"))
            run_id = str(config.get("runId", "")).strip()
            model_id = str(config.get("modelId", "")).strip()
            if not run_id or not model_id:
                raise ValueError("runId and modelId are required when exporting an execution plan")
            source = source.rename(columns={"datetime": "trade_date", "pred_score": "score"})
            targets = build_target_positions(
                source,
                top_k=int(config.get("topN", cfg.top_k)),
                max_position_weight=cfg.max_position_weight,
                max_industry_weight=cfg.max_industry_weight,
                run_id=run_id,
                model_id=model_id,
            )
            output = _resolve_runtime_path(
                config.get("outputPath"), "output", "research", "target_positions.json",
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            target_positions = [{
                "symbol": item.symbol,
                "rebalanceDate": item.rebalance_date.isoformat(),
                "targetWeight": item.target_weight,
                "reason": item.reason,
                "riskFlags": list(item.risk_flags),
                "runId": item.run_id,
                "modelId": item.model_id,
                "signalId": item.signal_id,
            } for item in targets]
            output.write_text(json.dumps(target_positions, ensure_ascii=False, indent=2), encoding="utf-8")
            plans = []
            for rebalance_date in sorted({item.rebalance_date for item in targets}):
                group = [item for item in targets if item.rebalance_date == rebalance_date]
                plans.append(build_execution_plan(
                    group, mode="paper", run_id=run_id, model_id=model_id,
                ).to_dict())
            plan_dir = output.with_name(f"{output.stem}.execution-plans")
            plan_paths: list[str] = []
            for plan in plans:
                rebalance_date = str(plan["rebalance_date"])
                plan_path = plan_dir / f"{rebalance_date}.json"
                plan_path.parent.mkdir(parents=True, exist_ok=True)
                plan_path.write_text(
                    json.dumps(plan, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                plan_paths.append(str(plan_path))
            # The singular path is the most recent signal that can reasonably
            # be reviewed for current Paper execution. Historical rebalance
            # plans remain available individually for replay and audit.
            latest_plan_path = plan_paths[-1] if plan_paths else ""
            as_of = max((item["rebalanceDate"] for item in target_positions), default="")
            envelope = {
                "schemaVersion": "1.0", "runId": run_id or model_id or "research-export",
                "strategyId": "aipha-ml-ranking", "strategyVersion": "1.0.0",
                "engine": "aipha", "dataAsOf": as_of, "signalFormat": "target_position",
                "signals": [], "targetPositions": target_positions,
            }
            return {"ok": True, "data": {
                "outputPath": str(output), "executionPlanPath": latest_plan_path,
                "executionPlanPaths": plan_paths,
                "count": len(targets), "runEnvelope": envelope,
            }}
        if action == "execute_paper":
            # Standard Paper-only execution boundary.  A plan must be
            # explicitly confirmed and is never routed to QMT from here.
            from aipha.research.execution_plan import read_execution_plan
            if config.get("confirm") is not True:
                raise ValueError("execute_paper requires confirm=true")
            plan_path = config.get("planPath")
            if not plan_path:
                raise ValueError("planPath is required")
            plan = read_execution_plan(plan_path, allow_live=False)
            if plan.mode != "paper":
                raise ValueError("execute_paper accepts paper plans only")
            prices = config.get("prices")
            if not isinstance(prices, dict):
                raise ValueError("prices must be an object keyed by symbol")
            account_value = float(config.get("accountValue", 0))
            if account_value <= 0:
                raise ValueError("accountValue must be positive")
            cfg = _load_config(config.get("configPath"))
            idempotency_key = str(config.get("idempotencyKey", "")).strip()
            receipt_root = _resolve_runtime_path(
                os.environ.get("QUANT_DATA_DIR"), "data",
            ).expanduser().resolve()
            receipt_path = receipt_root / "research" / "paper-executions.jsonl"
            if idempotency_key and receipt_path.is_file():
                for line in receipt_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        existing = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if existing.get("idempotency_key") == idempotency_key:
                        if existing.get("plan_id") != plan.plan_id:
                            raise ValueError("idempotencyKey was already used for a different execution plan")
                        return {"ok": True, "data": existing["response"]}
            from aipha.execution.paper_gateway import PaperGateway
            gateway = PaperGateway.from_cn_equity(initial_capital=account_value)
            order_ids = gateway.submit_target_plan(
                plan,
                prices={str(key): float(value) for key, value in prices.items()},
                account_value=account_value,
                max_position_weight=float(config.get("maxPositionWeight", cfg.max_position_weight)),
                max_total_weight=float(config.get("maxTotalWeight", 1.0)),
            )
            # The bridge process is short-lived, so keep a durable execution
            # receipt after the in-memory PaperGateway has accepted the plan.
            # This is intentionally a receipt, not a broker order database;
            # it lets operators reconcile a retry without routing anything to
            # QMT.
            import datetime as _datetime
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            orders = []
            for order_id in order_ids:
                order = gateway.get_order(order_id)
                if order is None:
                    continue
                orders.append({
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "qty": order.qty,
                    "order_type": order.order_type,
                    "status": str(order.status),
                    "metadata": dict(order.metadata),
                })
            receipt = {
                "timestamp": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
                "schema_version": 1,
                "plan_id": plan.plan_id,
                "mode": "paper",
                "run_id": plan.run_id,
                "model_id": plan.model_id,
                "signal_id": plan.signal_id,
                "idempotency_key": idempotency_key,
                "order_ids": order_ids,
                "orders": orders,
            }
            response = {
                "planId": plan.plan_id,
                "mode": "paper",
                "orderIds": order_ids,
                "count": len(order_ids),
                "provenance": {
                    "runId": plan.run_id,
                    "modelId": plan.model_id,
                    "signalId": plan.signal_id,
                },
                "receiptPath": str(receipt_path),
            }
            receipt["response"] = response
            with receipt_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt, ensure_ascii=False, default=str) + "\n")
            return {"ok": True, "data": response}
        return {
            "ok": False,
            "error": (
                f"未知 action: {action}; use the canonical ml_ranking actions "
                "with a versioned Parquet snapshot"
            ),
        }
    except Exception as error:
        return {"ok": False, "error": str(error)}


if __name__ == "__main__":
    try:
        request = json.loads(os.environ.get("QUANT_CONFIG", "{}"))
        print(json.dumps(run(request), ensure_ascii=False, default=str))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error), "trace": traceback.format_exc()}, ensure_ascii=False))
