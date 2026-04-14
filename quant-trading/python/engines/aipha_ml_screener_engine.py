"""Bridge engine: ML ranking screener — 接收 JSON 请求，调用 ML 排序选股器"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def main():
    request = json.load(sys.stdin)
    action = request.get("action", "screen")
    params = request.get("params", {})

    if action == "list":
        from screeners.base import list_screener_ids
        result = {"screeners": list_screener_ids()}
        print(json.dumps(result, ensure_ascii=False))
        return

    if action == "screen":
        from screeners.base import get_screener
        screener_id = request.get("screener_id", "ml_ranking")
        stock_data = request.get("stock_data", {})
        market = request.get("market", "cn_stock")
        limit = request.get("limit", 30)

        screener = get_screener(screener_id, params)
        if screener is None:
            from screeners.base import list_screener_ids
            result = {
                "error": True,
                "message": f"未知选股器 '{screener_id}'",
                "available": list_screener_ids(),
            }
            print(json.dumps(result, ensure_ascii=False))
            return

        results = screener.screen(stock_data, market=market, limit=limit)
        result = {
            "screener_id": screener_id,
            "screener_name": screener.screener_name,
            "total": len(results),
            "results": [r.to_dict() for r in results],
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    print(json.dumps({"error": True, "message": f"未知 action: {action}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
