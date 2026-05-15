"""Check financial provider fields."""
import sys
sys.path.insert(0, ".")
from data_providers.financial_provider import fetch_financials

snap = fetch_financials("600519", market="cn_stock")
if snap:
    for attr in ["pe", "pe_ttm", "pb", "eps", "roe", "roa", "roic",
                 "gross_margin", "net_margin", "revenue_growth_yoy",
                 "net_profit_growth_yoy", "debt_ratio", "f_score",
                 "total_market_val_yi", "price", "name"]:
        print(f"{attr}: {getattr(snap, attr, None)}")
else:
    print("No data")
