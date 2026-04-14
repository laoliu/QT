"""Aipha 可转债桥接引擎 — 为 TS tools 提供 CB 分析服务"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime


def main() -> None:
    raw = sys.stdin.read()
    request = json.loads(raw)
    action = request.get("action", "list_bonds")

    try:
        if action == "list_bonds":
            result = _list_bonds(request)
        elif action == "metrics":
            result = _compute_metrics(request)
        elif action == "factors":
            result = _compute_factors(request)
        elif action == "select":
            result = _strategy_select(request)
        elif action == "delta_zone":
            result = _delta_zone(request)
        else:
            result = {"error": f"Unknown action: {action}"}

        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))


def _list_bonds(req: dict) -> dict:
    from aipha.data.cb_provider import ConvertibleBondProvider
    source = req.get("source", "tushare")
    provider = ConvertibleBondProvider(source=source)
    bonds = provider.load_bond_list()
    return {
        "count": len(bonds),
        "bonds": [
            {
                "ts_code": b.ts_code,
                "name": b.bond_short_name,
                "stk_code": b.stk_code,
                "conv_price": float(b.conv_price),
                "issue_size": float(b.issue_size),
                "days_to_maturity": b.days_to_maturity,
                "rating": b.rating,
            }
            for b in bonds[:100]  # 限制返回数量
        ],
    }


def _compute_metrics(req: dict) -> dict:
    from aipha.data.cb_provider import ConvertibleBondProvider
    source = req.get("source", "tushare")
    symbols = req.get("symbols", [])
    provider = ConvertibleBondProvider(source=source)
    results = {}
    for sym in symbols:
        bonds = provider.load_bond_list()
        bond = next((b for b in bonds if b.ts_code == sym), None)
        if bond:
            # 简化：使用转股价计算
            stock_price = float(bond.conv_price) * 0.9  # 占位
            metrics = provider.compute_metrics(bond, 100.0, stock_price)
            results[sym] = {
                "conv_value": metrics.conv_value,
                "conv_premium": metrics.conv_premium,
                "delta_proxy": metrics.delta_proxy,
                "double_low": metrics.double_low,
                "ytm_estimate": metrics.ytm_estimate,
            }
    return {"metrics": results}


def _compute_factors(req: dict) -> dict:
    from aipha.factors.cb_factors import compute_all_factors
    symbols = req.get("symbols", [])
    now = datetime.now()
    results = {}
    for sym in symbols:
        # 实际使用时需要从数据源获取行情
        fv = compute_all_factors(
            symbol=sym, close=100.0, cb_value=90.0,
            amount=5e6, issue_size=1e4,
            maturity_date="20280101", now=now,
        )
        results[sym] = fv.as_dict()
    return {"factors": results}


def _strategy_select(req: dict) -> dict:
    from aipha.strategy.cb_strategies import get_cb_strategy, CandidateInfo, CBStrategyConfig
    strategy_name = req.get("strategy", "cb_double_low")
    top_k = req.get("top_k", 20)
    max_price = req.get("max_price", 130)
    now = datetime.now()

    # 从数据源加载候选 — 此处为框架代码，实际需要接入行情数据
    config = CBStrategyConfig(top_k=top_k, max_price=max_price)
    strategy = get_cb_strategy(strategy_name, config=config)
    # 实际调用时传入真实候选数据
    selected = strategy.select([], now)
    return {
        "strategy": strategy_name,
        "selected": selected,
        "count": len(selected),
    }


def _delta_zone(req: dict) -> dict:
    from aipha.factors.cb_factors import classify_delta_zone
    symbols = req.get("symbols", [])
    results = {}
    for sym in symbols:
        zone = classify_delta_zone(cb_value=90.0)  # 占位
        results[sym] = {"zone": zone}
    return {"zones": results}


if __name__ == "__main__":
    main()
