"""检查所有配置文件中的费率参数一致性."""
import yaml, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def check_cb_configs():
    """Check all CB config files."""
    cb_dir = ROOT / "configs" / "cb_strategies"
    issues = []
    mc_files = []
    for f in sorted(cb_dir.rglob("*.yml")):
        with open(f, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if not cfg:
            continue
        cost = cfg.get("cost", {})
        probs = []
        cm = cost.get("commission_min")
        mc = cost.get("min_commission")
        cr = cost.get("commission_rate")
        st = cost.get("stamp_tax_rate")
        tf = cost.get("transfer_fee_rate")

        if cm is not None and float(cm) != 5.0:
            probs.append(f"commission_min={cm} (should be 5)")
        if mc is not None:
            mc_files.append((str(f.relative_to(ROOT)), mc))
            if float(mc) != 5.0:
                probs.append(f"min_commission={mc} (should be 5)")
        if cr is not None and abs(float(cr) - 8.54e-05) > 1e-8:
            probs.append(f"commission_rate={cr} (should be 8.54e-05)")
        if st is not None and float(st) != 0.0:
            probs.append(f"stamp_tax_rate={st} (should be 0 for CB)")
        if tf is not None and float(tf) != 0.0:
            probs.append(f"transfer_fee_rate={tf} (should be 0 for CB)")
        if probs:
            issues.append((str(f.relative_to(ROOT)), probs))

    print(f"=== CB Configs ({len(list(cb_dir.rglob('*.yml')))} files) ===")
    if issues:
        print(f"Found {len(issues)} files with issues:")
        for fn, ps in issues:
            for p in ps:
                print(f"  {fn}: {p}")
    else:
        print("  All CB config files OK!")
    if mc_files:
        print(f"\n  Files using 'min_commission' key (alias): {len(mc_files)}")
        for fn, v in mc_files[:5]:
            print(f"    {fn}: min_commission={v}")


def check_astock_configs():
    """Check A-stock backtest configs."""
    bt_dir = ROOT / "configs" / "backtest"
    issues = []
    for f in sorted(bt_dir.rglob("*.yml")):
        with open(f, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if not cfg:
            continue
        cost = cfg.get("cost", {})
        probs = []
        cr = cost.get("commission_rate")
        cm = cost.get("commission_min")
        mc = cost.get("min_commission")
        st = cost.get("stamp_tax_rate")
        tf = cost.get("transfer_fee_rate")

        if cr is not None and abs(float(cr) - 8.54e-05) > 1e-8:
            probs.append(f"commission_rate={cr}")
        if cm is not None and float(cm) != 5.0:
            probs.append(f"commission_min={cm}")
        if mc is not None and float(mc) != 5.0:
            probs.append(f"min_commission={mc}")
        if st is not None and abs(float(st) - 0.0005) > 1e-8:
            probs.append(f"stamp_tax_rate={st}")
        if tf is not None and abs(float(tf) - 0.00001) > 1e-8:
            probs.append(f"transfer_fee_rate={tf}")
        if probs:
            issues.append((str(f.relative_to(ROOT)), probs))

    print(f"\n=== A-Stock Configs ({len(list(bt_dir.rglob('*.yml')))} files) ===")
    if issues:
        print(f"Found {len(issues)} files with issues:")
        for fn, ps in issues:
            for p in ps:
                print(f"  {fn}: {p}")
    else:
        print("  All A-stock config files OK!")


def check_paper_configs():
    """Check paper trading configs."""
    paper_dir = ROOT / "configs" / "paper"
    issues = []
    for f in sorted(paper_dir.rglob("*.yml")):
        with open(f, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if not cfg:
            continue
        # Check engine section
        eng = cfg.get("engine", {})
        cr = eng.get("commission_rate")
        cm = eng.get("commission_min")
        if cr is not None and abs(float(cr) - 8.54e-05) > 1e-8:
            issues.append((str(f.relative_to(ROOT)), f"commission_rate={cr}"))
        if cm is not None and float(cm) != 5.0:
            issues.append((str(f.relative_to(ROOT)), f"commission_min={cm}"))

    print(f"\n=== Paper Configs ({len(list(paper_dir.rglob('*.yml')))} files) ===")
    if issues:
        print(f"Found {len(issues)} files with issues:")
        for fn, p in issues:
            print(f"  {fn}: {p}")
    else:
        print("  All paper config files OK!")


def check_env_files():
    """Check .env and .env.example."""
    for fn in [".env", ".env.example"]:
        fp = ROOT / fn
        if not fp.exists():
            continue
        with open(fp, encoding="utf-8") as f:
            lines = f.readlines()
        print(f"\n=== {fn} fee params ===")
        for line in lines:
            line = line.strip()
            if any(k in line.upper() for k in [
                "COMMISSION", "STAMP_TAX", "TRANSFER_FEE",
                "SLIPPAGE", "MIN_COMMISSION"
            ]):
                print(f"  {line}")


def check_fee_config():
    """Check scripts/fee_config.py."""
    fp = ROOT / "scripts" / "fee_config.py"
    if not fp.exists():
        return
    with open(fp, encoding="utf-8") as f:
        lines = f.readlines()
    print(f"\n=== scripts/fee_config.py ===")
    for i, line in enumerate(lines, 1):
        if any(k in line.lower() for k in [
            "commission", "stamp_tax", "transfer_fee", "slippage"
        ]):
            print(f"  L{i}: {line.rstrip()}")


if __name__ == "__main__":
    check_cb_configs()
    check_astock_configs()
    check_paper_configs()
    check_env_files()
    check_fee_config()
