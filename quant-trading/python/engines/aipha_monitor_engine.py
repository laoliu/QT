"""Bridge engine: aipha monitoring — 因子/策略健康监控"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def main():
    request = json.load(sys.stdin)
    action = request.get("action", "factor_health")

    if action == "factor_health":
        from aipha.monitoring.factor_decay_monitor import FactorDecayMonitor
        monitor = FactorDecayMonitor()
        factor_names = request.get("factor_names", [])
        result = {
            "action": "factor_health",
            "factors_checked": factor_names,
            "status": "ready",
            "note": "需提供因子 IC 历史数据",
        }

    elif action == "crowding":
        from aipha.monitoring.crowding_monitor import CrowdingMonitor
        monitor = CrowdingMonitor()
        result = {
            "action": "crowding",
            "strategy": request.get("strategy_name", ""),
            "status": "ready",
            "note": "需提供持仓权重和因子数据",
        }

    elif action == "retrain_check":
        from aipha.monitoring.retrain_trigger import RetrainTrigger
        trigger = RetrainTrigger()
        decision = trigger.check(psi_value=request.get("psi_value"))
        result = {
            "action": "retrain_check",
            "should_retrain": decision.should_retrain,
            "reasons": [r.value for r in decision.reasons],
            "days_since_last": decision.days_since_last,
        }

    else:
        result = {"error": True, "message": f"未知 action: {action}"}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
