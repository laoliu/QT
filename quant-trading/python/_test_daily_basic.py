"""Test tushare daily_basic loading."""
import sys
sys.path.insert(0, ".")

from aipha.data.tushare_equity_provider import TuShareEquityProvider
p = TuShareEquityProvider()
df = p.load_daily_basic("20260416")
print(f"Columns: {list(df.columns)}")
print(f"Rows: {len(df)}")
if len(df) > 0:
    print(df.head(3).to_string())
else:
    print("No data returned")
