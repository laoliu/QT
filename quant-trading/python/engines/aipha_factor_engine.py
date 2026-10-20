"""Aipha 因子分析引擎 — QT Python Bridge 入口。

调用方式: QUANT_CONFIG='{...}' python -m engines.aipha_factor_engine
"""
from __future__ import annotations

import json
import os
import sys
import traceback
import math
from datetime import date

# 确保 aipha 包可导入
_dir = os.path.dirname(os.path.abspath(__file__))
_parent = os.path.dirname(_dir)
if _parent not in sys.path:
    sys.path.insert(0, _parent)


def run(config: dict) -> dict:
    from aipha.factors.registry import _registry, list_factors, factor_categories
    from aipha.factors.base import FactorData, FactorResult
    import aipha.factors.core_factors  # 触发注册

    action = config.get("action", "list")

    if action == "list":
        return {
            "ok": True,
            "data": {
                "factors": list_factors(),
                "categories": factor_categories(),
            },
        }

    if action == "compute":
        factor_name = config.get("factor_name", "pe")
        symbols = config.get("symbols", [])
        trade_date_str = config.get("trade_date") or date.today().isoformat()
        trade_date_val = date.fromisoformat(trade_date_str)

        factor_cls = _registry.get(factor_name)
        if factor_cls is None:
            return {"ok": False, "error": f"因子 '{factor_name}' 未注册"}

        factor = _registry.create(factor_name)
        required_fields = {"pe": "pe_ttm", "pb": "pb", "ps": "ps_ttm", "dividend_yield": "dv_ttm", "roe": "roe", "roa": "roa", "gross_margin": "grossprofit_margin", "market_cap": "circ_mv", "turnover_rate": "turnover_rate"}
        if not symbols:
            return {"ok": False, "status": "insufficient_data", "error": "需要明确股票代码和因子输入数据"}
        missing = []
        for symbol in symbols:
            fundamentals = (config.get("fundamentals") or {}).get(symbol, {})
            returns = (config.get("returns") or {}).get(symbol, [])
            field = required_fields.get(factor_name)
            if field and (not isinstance(fundamentals.get(field), (int, float)) or not math.isfinite(fundamentals[field])):
                missing.append({"symbol": symbol, "field": field})
            if factor_name in {"momentum", "volatility"} and (len(returns) < 20 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in returns)):
                missing.append({"symbol": symbol, "field": "returns (at least 20 finite observations)"})
        if missing:
            return {"ok": False, "status": "insufficient_data", "missing": missing, "error": "缺少真实因子输入，未生成零值或排名"}
        results = []
        for symbol in symbols:
            data = FactorData(
                symbol=symbol,
                trade_date=trade_date_val,
                fundamentals=(config.get("fundamentals") or {}).get(symbol, {}),
                returns=(config.get("returns") or {}).get(symbol, []),
            )
            result = factor.compute(data)
            results.append({
                "symbol": result.symbol,
                "factor_name": result.factor_name,
                "value": result.value,
            })

        # 归一化
        if config.get("normalize"):
            factor_results = [
                FactorData(symbol=r["symbol"], trade_date=trade_date_val)
                for r in results
            ]
            # 重新构建 FactorResult 列表进行归一化
            fr_list = [
                FactorResult(
                    symbol=r["symbol"], trade_date=trade_date_val,
                    factor_name=r["factor_name"], value=r["value"],
                )
                for r in results
            ]
            normalized = factor.normalize(fr_list, method=config.get("normalize", "rank"))
            results = [
                {
                    "symbol": r.symbol,
                    "factor_name": r.factor_name,
                    "value": r.value,
                    "rank": r.rank,
                    "z_score": r.z_score,
                }
                for r in normalized
            ]

        return {"ok": True, "data": {"results": results}}

    return {"ok": False, "error": f"未知 action: {action}"}


if __name__ == "__main__":
    try:
        raw = os.environ.get("QUANT_CONFIG", "{}")
        config = json.loads(raw)
        result = run(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()}, ensure_ascii=False))
