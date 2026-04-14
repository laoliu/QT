"""
aipha_risk_gate_engine — 桥接引擎: 接收 JSON 请求，调用 aipha 风险约束体系
返回额外风控信号供 TS RiskGate 消费

输入 JSON:
  {
    "action": "check_portfolio" | "check_order" | "get_constraints",
    "positions": [...],
    "order": {...},
    "config": {...}
  }

输出 JSON:
  {
    "passed": true/false,
    "warnings": [...],
    "constraints": {...}
  }
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _check_portfolio(positions: list, config: dict) -> dict:
    """检查整体组合风控约束"""
    from aipha.risk.constraints import CompositeRiskManager, RiskConfig

    risk_cfg = RiskConfig(
        max_position_weight=config.get("max_position_weight", 0.05),
        max_drawdown=config.get("max_drawdown", 0.15),
        max_daily_turnover=config.get("max_daily_turnover", 0.30),
    )
    manager = CompositeRiskManager(risk_cfg)

    warnings = []

    # 检查各持仓权重
    total_value = sum(abs(p.get("market_value", 0)) for p in positions)
    if total_value <= 0:
        return {"passed": True, "warnings": [], "total_value": 0}

    for p in positions:
        weight = abs(p.get("market_value", 0)) / total_value
        if weight > risk_cfg.max_position_weight:
            warnings.append({
                "rule": "MAX_POSITION_WEIGHT",
                "symbol": p.get("symbol", ""),
                "weight": round(weight, 4),
                "limit": risk_cfg.max_position_weight,
                "message": f"{p.get('symbol', '')} 权重 {weight:.1%} 超限 {risk_cfg.max_position_weight:.0%}",
            })

    # 行业集中度检查
    sector_weights: dict = {}
    for p in positions:
        sector = p.get("sector", "unknown")
        sector_weights[sector] = sector_weights.get(sector, 0) + abs(p.get("market_value", 0)) / total_value

    max_sector = config.get("max_sector_weight", 0.25)
    for sector, w in sector_weights.items():
        if w > max_sector:
            warnings.append({
                "rule": "MAX_SECTOR_WEIGHT",
                "sector": sector,
                "weight": round(w, 4),
                "limit": max_sector,
                "message": f"行业 {sector} 权重 {w:.1%} 超限 {max_sector:.0%}",
            })

    # HHI 集中度
    weights = [abs(p.get("market_value", 0)) / total_value for p in positions]
    hhi = sum(w ** 2 for w in weights)
    max_hhi = config.get("max_hhi", 0.15)
    if hhi > max_hhi:
        warnings.append({
            "rule": "HHI_CONCENTRATION",
            "hhi": round(hhi, 4),
            "limit": max_hhi,
            "message": f"持仓集中度 HHI={hhi:.4f} 超限 {max_hhi}",
        })

    return {
        "passed": len(warnings) == 0,
        "warnings": warnings,
        "total_value": total_value,
        "hhi": round(hhi, 4),
        "sector_weights": {k: round(v, 4) for k, v in sector_weights.items()},
    }


def _check_order(order: dict, positions: list, config: dict) -> dict:
    """检查单笔订单的 aipha 风控约束"""
    warnings = []

    symbol = order.get("symbol", "")
    side = order.get("side", "buy")
    quantity = order.get("quantity", 0)
    price = order.get("price", 0)
    order_value = price * quantity

    total_value = sum(abs(p.get("market_value", 0)) for p in positions)
    if total_value <= 0:
        total_value = order_value * 10  # 估算

    # 因子拥挤度检查
    crowding_threshold = config.get("crowding_threshold", 0.7)
    crowding_score = order.get("crowding_score", 0)
    if crowding_score > crowding_threshold:
        warnings.append({
            "rule": "FACTOR_CROWDING",
            "symbol": symbol,
            "crowding_score": crowding_score,
            "threshold": crowding_threshold,
            "message": f"{symbol} 因子拥挤度 {crowding_score:.2f} 超限 {crowding_threshold}",
        })

    # 因子衰减检查
    decay_threshold = config.get("decay_threshold", 0.5)
    factor_ic = order.get("factor_ic", 1.0)
    if factor_ic < decay_threshold:
        warnings.append({
            "rule": "FACTOR_DECAY",
            "symbol": symbol,
            "factor_ic": factor_ic,
            "threshold": decay_threshold,
            "message": f"{symbol} 因子 IC={factor_ic:.3f} 衰减严重 < {decay_threshold}",
        })

    # 流动性检查
    avg_turnover = order.get("avg_daily_turnover", 0)
    min_turnover = config.get("min_daily_turnover", 0)
    if min_turnover > 0 and avg_turnover > 0:
        participation = order_value / avg_turnover if avg_turnover > 0 else 1.0
        max_participation = config.get("max_participation_rate", 0.05)
        if participation > max_participation:
            warnings.append({
                "rule": "LIQUIDITY_IMPACT",
                "symbol": symbol,
                "participation_rate": round(participation, 4),
                "limit": max_participation,
                "message": f"{symbol} 订单占日成交 {participation:.1%} 超限 {max_participation:.0%}",
            })

    return {
        "passed": len(warnings) == 0,
        "warnings": warnings,
    }


def _get_constraints(config: dict) -> dict:
    """返回当前 aipha 风控配置"""
    return {
        "max_position_weight": config.get("max_position_weight", 0.05),
        "max_drawdown": config.get("max_drawdown", 0.15),
        "max_daily_turnover": config.get("max_daily_turnover", 0.30),
        "max_sector_weight": config.get("max_sector_weight", 0.25),
        "max_hhi": config.get("max_hhi", 0.15),
        "crowding_threshold": config.get("crowding_threshold", 0.7),
        "decay_threshold": config.get("decay_threshold", 0.5),
        "max_participation_rate": config.get("max_participation_rate", 0.05),
    }


def main():
    request = json.load(sys.stdin)
    action = request.get("action", "check_portfolio")
    positions = request.get("positions", [])
    order = request.get("order", {})
    config = request.get("config", {})

    if action == "check_portfolio":
        result = _check_portfolio(positions, config)
    elif action == "check_order":
        result = _check_order(order, positions, config)
    elif action == "get_constraints":
        result = _get_constraints(config)
    else:
        result = {"error": True, "message": f"未知 action: {action}"}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
