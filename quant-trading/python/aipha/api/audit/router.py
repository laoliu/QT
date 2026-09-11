"""Audit & trade statistics API routes.

Provides endpoints for:
- Annual trade statistics (议题 5)
- Trade record query with pagination (议题 9)
- Participation rate config (议题 7)
- Yearly attribution analysis (议题 9)
"""

from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from aipha.api.schemas import ApiResponse
from aipha.utils.compliance_config import DEFAULT_COMPLIANCE_CONFIG_PATH
from aipha.utils.paths import project_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"])

# Output directory (backtest results)
# 锚到项目根，不吃当前工作目录：API 由 uvicorn / docker 从别的目录拉起时，
# Path("output") 会指到一个空目录，接口于是返回"没有回测结果"而不是报错。
_OUTPUT_DIR = project_path("output")


def _find_strategy_dirs() -> list[dict[str, Any]]:
    """Scan output/ for strategy directories that contain trades.csv."""
    results = []
    if not _OUTPUT_DIR.is_dir():
        return results
    for d in sorted(_OUTPUT_DIR.iterdir()):
        if not d.is_dir():
            continue
        trades_csv = d / "trades.csv"
        summary_json = d / "summary.json"
        if not trades_csv.is_file():
            continue
        info: dict[str, Any] = {
            "name": d.name,
            "has_trades": True,
            "has_summary": summary_json.is_file(),
            "trades_size_kb": round(trades_csv.stat().st_size / 1024, 1),
        }
        # Read basic metrics from summary.json if available
        if summary_json.is_file():
            try:
                with open(summary_json, encoding="utf-8") as f:
                    sm = json.load(f)
                metrics = sm.get("metrics", sm)
                info["annual_return"] = metrics.get("annual_return")
                info["sharpe_ratio"] = metrics.get("sharpe_ratio")
                info["max_drawdown"] = metrics.get("max_drawdown")
            except Exception:
                pass
        results.append(info)
    return results


def _read_trades_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Read a trades.csv file and return list of trade dicts."""
    trades: list[dict[str, Any]] = []
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trade: dict[str, Any] = {}
                for k, v in row.items():
                    # Convert numeric fields
                    if k in (
                        "qty", "price", "notional", "commission",
                        "stamp_tax", "transfer_fee", "total_fee",
                        "amount", "fill_price", "fill_qty",
                    ):
                        try:
                            trade[k] = float(v) if "." in str(v) else int(v)
                        except (ValueError, TypeError):
                            trade[k] = v
                    else:
                        trade[k] = v
                trades.append(trade)
    except Exception as e:
        logger.error("Failed to read %s: %s", csv_path, e)
    return trades


def _compute_yearly_stats(trades: list[dict]) -> list[dict[str, Any]]:
    """Aggregate trades into yearly statistics."""
    yearly: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "trade_count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "symbols": set(),
        "buy_amount": 0.0,
        "sell_amount": 0.0,
        "total_fees": 0.0,
        "commission": 0.0,
        "stamp_tax": 0.0,
    })
    for t in trades:
        # Extract year from date field
        date_str = t.get("date", "") or t.get("trade_date", "")
        if not date_str:
            continue
        year = str(date_str)[:4]
        y = yearly[year]
        y["trade_count"] += 1
        symbol = t.get("symbol", "")
        if symbol:
            y["symbols"].add(symbol)
        side = str(t.get("side", "")).upper()
        notional = float(t.get("notional", 0) or t.get("amount", 0) or 0)
        if side == "BUY":
            y["buy_count"] += 1
            y["buy_amount"] += notional
        elif side == "SELL":
            y["sell_count"] += 1
            y["sell_amount"] += notional
        y["total_fees"] += float(t.get("total_fee", 0) or 0)
        y["commission"] += float(t.get("commission", 0) or 0)
        y["stamp_tax"] += float(t.get("stamp_tax", 0) or 0)

    result = []
    for year in sorted(yearly.keys()):
        y = yearly[year]
        result.append({
            "year": int(year),
            "trade_count": y["trade_count"],
            "buy_count": y["buy_count"],
            "sell_count": y["sell_count"],
            "unique_symbols": len(y["symbols"]),
            "buy_amount": round(y["buy_amount"], 2),
            "sell_amount": round(y["sell_amount"], 2),
            "net_amount": round(y["sell_amount"] - y["buy_amount"], 2),
            "total_fees": round(y["total_fees"], 2),
            "commission": round(y["commission"], 2),
            "stamp_tax": round(y["stamp_tax"], 2),
        })
    return result


@router.get("/strategies")
async def list_auditable_strategies() -> ApiResponse:
    """List all strategies that have trade data available for audit."""
    strategies = _find_strategy_dirs()
    return ApiResponse(success=True, data=strategies)


@router.get("/trades")
async def query_trades(
    strategy: str = Query(..., description="Strategy output directory name"),
    year: int | None = Query(None, description="Filter by year"),
    symbol: str | None = Query(None, description="Filter by symbol"),
    side: str | None = Query(None, description="Filter by side (BUY/SELL)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=10, le=1000),
) -> ApiResponse:
    """Query trade records with pagination and filters."""
    csv_path = _OUTPUT_DIR / strategy / "trades.csv"
    if not csv_path.is_file():
        raise HTTPException(404, f"No trades.csv found for strategy: {strategy}")

    trades = _read_trades_csv(csv_path)

    # Apply filters
    if year is not None:
        yr_str = str(year)
        trades = [
            t for t in trades
            if str(t.get("date", "") or t.get("trade_date", "")).startswith(yr_str)
        ]
    if symbol:
        trades = [t for t in trades if t.get("symbol", "") == symbol]
    if side:
        trades = [
            t for t in trades if str(t.get("side", "")).upper() == side.upper()
        ]

    total = len(trades)
    start = (page - 1) * page_size
    end = start + page_size
    page_data = trades[start:end]

    return ApiResponse(
        success=True,
        data={
            "trades": page_data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            },
        },
    )


@router.get("/yearly-summary")
async def yearly_summary(
    strategy: str = Query(..., description="Strategy output directory name"),
) -> ApiResponse:
    """Get annual trade statistics for a strategy."""
    csv_path = _OUTPUT_DIR / strategy / "trades.csv"
    if not csv_path.is_file():
        raise HTTPException(404, f"No trades.csv found for strategy: {strategy}")

    trades = _read_trades_csv(csv_path)
    stats = _compute_yearly_stats(trades)

    # Also load yearly returns from summary.json if available
    summary_path = _OUTPUT_DIR / strategy / "summary.json"
    yearly_returns: dict[str, float] = {}
    if summary_path.is_file():
        try:
            with open(summary_path, encoding="utf-8") as f:
                sm = json.load(f)
            yr = sm.get("yearly_returns", {})
            yearly_returns = {str(k): float(v) for k, v in yr.items()}
        except Exception:
            pass

    # Merge yearly returns into stats
    for s in stats:
        yr_key = str(s["year"])
        s["yearly_return"] = yearly_returns.get(yr_key)

    return ApiResponse(success=True, data={"strategy": strategy, "yearly": stats})


@router.get("/risk-config")
async def get_risk_config() -> ApiResponse:
    """Get current participation rate and risk configuration."""
    # Read from algo_trading_compliance.yml if exists.
    # 用包内解析出的绝对路径，不要用相对 CWD 的路径 —— API 进程的工作目录
    # 不一定是 python/，那样会静默返回 source="default" 而不是真实配置。
    config_path = DEFAULT_COMPLIANCE_CONFIG_PATH
    config: dict[str, Any] = {
        "max_participation_rate": 0.10,
        "max_participation_rate_crowded": 0.05,
        "source": "default",
    }
    if config_path.is_file():
        try:
            import yaml
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            participation = cfg.get("participation", {})
            config["max_participation_rate"] = participation.get(
                "max_participation_rate", 0.10,
            )
            config["max_participation_rate_crowded"] = participation.get(
                "crowding_high_participation_rate", 0.05,
            )
            config["source"] = str(config_path)
        except Exception as e:
            logger.error("Failed to read risk config: %s", e)

    return ApiResponse(success=True, data=config)


@router.put("/risk-config")
async def update_risk_config(
    max_participation_rate: float = Query(
        ..., ge=0.01, le=0.30,
        description="Max participation rate (0.01 - 0.30)",
    ),
) -> ApiResponse:
    """Update participation rate configuration."""
    config_path = DEFAULT_COMPLIANCE_CONFIG_PATH
    if not config_path.is_file():
        raise HTTPException(404, "Config file not found")

    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        if "participation" not in cfg:
            cfg["participation"] = {}
        cfg["participation"]["max_participation_rate"] = max_participation_rate

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)

        return ApiResponse(
            success=True,
            data={
                "max_participation_rate": max_participation_rate,
                "message": "Configuration updated successfully",
            },
        )
    except Exception as e:
        raise HTTPException(500, f"Failed to update config: {e}") from e
