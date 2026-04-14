"""Bridge engine: equity screening — 接收 JSON 请求，调用 aipha 策略选股"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def main():
    request = json.load(sys.stdin)
    strategy_name = request.get("strategy", "dividend_quality")
    top_k = request.get("top_k", 30)
    universe_type = request.get("universe", "ALL_A")

    # 注册所有策略
    import aipha.strategy.register_all  # noqa: F401
    from aipha.strategy.registry import StrategyRegistry

    registry = StrategyRegistry()
    available = registry.list_strategies()

    if strategy_name not in available:
        result = {
            "error": True,
            "message": f"未知策略 '{strategy_name}'",
            "available_strategies": available,
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    strategy_cls = registry.get(strategy_name)
    result = {
        "strategy": strategy_name,
        "universe": universe_type,
        "top_k": top_k,
        "available_strategies": available,
        "total_registered": len(available),
        "status": "ready",
        "note": "策略已注册，需配合实时行情数据运行",
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
