"""Test batch A-stock spot data with PE/PB."""
import sys
sys.path.insert(0, ".")
import akshare as ak

# This returns ALL A-stocks with real-time data
print("=== stock_zh_a_spot_em (ALL A-stocks) ===")
try:
    df = ak.stock_zh_a_spot_em()
    print(f"Rows: {len(df)}")
    print(f"Columns: {list(df.columns)}")
    # Find PE/PB columns
    pe_cols = [c for c in df.columns if "市盈" in c or "PE" in c.upper()]
    pb_cols = [c for c in df.columns if "市净" in c or "PB" in c.upper()]
    print(f"PE cols: {pe_cols}")
    print(f"PB cols: {pb_cols}")
    # Show sample for 600519
    row = df[df["代码"] == "600519"]
    if not row.empty:
        print(f"\n600519 data:")
        for col in row.columns:
            print(f"  {col}: {row.iloc[0][col]}")
except Exception as e:
    print(f"Failed: {e}")
    import traceback
    traceback.print_exc()
