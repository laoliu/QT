"""Quick diagnostic for strategy fitness backtest — new architecture."""
import sys, json, math, traceback, time
sys.path.insert(0, ".")

def main():
    from scripts.strategy_fitness import (
        _get_astock_strategies,
        _load_klines,
        _load_fundamentals,
        _build_bars_by_date,
        _run_strategy_backtest,
    )
    from datetime import datetime, timedelta

    # Use 10 stocks as test
    test_symbols = ["600519", "000858", "601318", "000001", "600036",
                    "002415", "000333", "601888", "300750", "603259"]

    # Step 1: Get strategies
    print("=== Step 1: Strategies ===", flush=True)
    sids = _get_astock_strategies()
    print(f"Count: {len(sids)}", flush=True)
    test_sids = sids[:3]  # Only test 3 strategies
    print(f"Testing: {test_sids}", flush=True)

    # Step 2: Load klines
    print("\n=== Step 2: Load klines ===", flush=True)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=2 * 365)).strftime("%Y-%m-%d")
    klines = _load_klines(test_symbols, start_date, end_date)
    print(f"Loaded klines for {len(klines)} stocks", flush=True)
    for sym, bars in list(klines.items())[:3]:
        print(f"  {sym}: {len(bars)} bars", flush=True)

    # Step 3: Load fundamentals
    print("\n=== Step 3: Load fundamentals ===", flush=True)
    valid_symbols = [s for s in test_symbols if s in klines and len(klines[s]) >= 60]
    fundamentals = _load_fundamentals(valid_symbols)
    for sym in valid_symbols[:3]:
        f = fundamentals.get(sym, {})
        print(f"  {sym}: pe={f.get('pe_ttm')}, pb={f.get('pb')}, roe={f.get('roe')}, dv={f.get('dv_ratio')}", flush=True)

    # Step 4: Build bars_by_date
    print("\n=== Step 4: Build bars_by_date ===", flush=True)
    filtered_klines = {s: klines[s] for s in valid_symbols}
    bars_by_date = _build_bars_by_date(filtered_klines, fundamentals)
    print(f"Trading days: {len(bars_by_date)}", flush=True)
    first_date = sorted(bars_by_date.keys())[0]
    print(f"First date stocks: {len(bars_by_date[first_date])}", flush=True)
    # Check metadata on first bar
    first_sym = list(bars_by_date[first_date].keys())[0]
    first_bar = bars_by_date[first_date][first_sym]
    print(f"Sample bar: {first_sym}, metadata={first_bar.metadata}", flush=True)

    # Step 5: Test strategy backtest
    print("\n=== Step 5: Test strategy backtest ===", flush=True)
    for sid in test_sids:
        t0 = time.time()
        per_stock = _run_strategy_backtest(sid, bars_by_date, 1_000_000)
        elapsed = time.time() - t0
        if per_stock:
            print(f"\n  {sid}: {len(per_stock)} stocks traded ({elapsed:.1f}s)", flush=True)
            for sym, m in list(per_stock.items())[:5]:
                print(f"    {sym}: score={m['composite_score']:.4f}, return={m['total_return']:.2f}%, trades={m['total_trades']}", flush=True)
        else:
            print(f"\n  {sid}: NO TRADES ({elapsed:.1f}s)", flush=True)

if __name__ == "__main__":
    main()
