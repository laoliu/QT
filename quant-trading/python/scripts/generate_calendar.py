"""
交易日历数据生成脚本

通过 AKShare 的 `ak.tool_trade_date_hist_sina()` 获取真实交易日数据，
生成 JSON 文件供 CNStockCalendar 使用。

用法:
    python scripts/generate_calendar.py

输出:
    data/calendars/cn_stock_v1.json

注意:
    需要安装 akshare: pip install akshare
"""

import json
import logging
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def generate_calendar(output_path: Path | None = None) -> list[str]:
    """生成交易日历数据

    Returns:
        交易日字符串列表 ["2000-01-04", "2000-01-05", ...]
    """
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare 未安装，请 pip install akshare")
        raise

    logger.info("正在从新浪获取交易日历数据...")
    df = ak.tool_trade_date_hist_sina()

    # DataFrame 应有一列 trade_date
    if "trade_date" not in df.columns:
        logger.warning(f"列名: {df.columns.tolist()}, 尝试使用第一列")
        trade_dates = df.iloc[:, 0]
    else:
        trade_dates = df["trade_date"]

    # 转为 date 列表
    trading_days = sorted(set(
        d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        for d in trade_dates
    ))

    logger.info(f"获取到 {len(trading_days)} 个交易日")
    logger.info(f"日期范围: {trading_days[0]} ~ {trading_days[-1]}")

    # 验证数据质量
    _validate(trading_days)

    # 保存
    if output_path is None:
        output_path = Path("data/calendars/cn_stock_v1.json")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    calendar_data = {
        "version": "1.0",
        "source": "sina_via_akshare",
        "generated_at": date.today().isoformat(),
        "count": len(trading_days),
        "range": {
            "start": trading_days[0],
            "end": trading_days[-1],
        },
        "trading_days": trading_days,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(calendar_data, f, ensure_ascii=False, indent=2)

    logger.info(f"交易日历已保存: {output_path}")
    return trading_days


def _validate(trading_days: list[str]) -> None:
    """验证交易日历数据质量"""
    from datetime import date as dt_date

    dates = [dt_date.fromisoformat(d) for d in trading_days]

    # 检查排序
    for i in range(1, len(dates)):
        if dates[i] <= dates[i - 1]:
            logger.error(f"交易日未排序: {dates[i-1]} >= {dates[i]}")
            break

    # 检查无周末
    weekend_count = sum(1 for d in dates if d.weekday() >= 5)
    if weekend_count > 0:
        logger.warning(f"交易日中包含 {weekend_count} 个周末日期")

    # 年均交易日数合理性（约 240-250 天）
    if len(dates) >= 2:
        years = (dates[-1] - dates[0]).days / 365.25
        avg_per_year = len(dates) / years if years > 0 else 0
        if avg_per_year < 200 or avg_per_year > 270:
            logger.warning(f"年均交易日数异常: {avg_per_year:.0f}")
        else:
            logger.info(f"年均交易日数: {avg_per_year:.0f}")

    # 检查关键假期不在交易日中（抽样验证）
    key_holidays = {
        dt_date(2024, 2, 12): "春节",
        dt_date(2024, 10, 1): "国庆",
        dt_date(2024, 1, 1): "元旦",
        dt_date(2024, 5, 1): "劳动节",
        dt_date(2024, 6, 10): "端午",
        dt_date(2024, 9, 17): "中秋",
        dt_date(2024, 4, 4): "清明",
    }
    for holiday, name in key_holidays.items():
        if holiday in set(dates):
            logger.warning(f"关键假期 {name}({holiday}) 仍在交易日中")
        else:
            logger.info(f"✓ {name}({holiday}) 正确不在交易日中")


if __name__ == "__main__":
    generate_calendar()
