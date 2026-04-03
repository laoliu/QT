#!/usr/bin/env python3
"""
ensure_clash_direct — 确保 Clash 配置包含国内金融数据源的 DIRECT 规则

订阅更新会覆盖 iKuuu_V2.yaml，导致手动添加的 DIRECT 规则丢失。
此脚本检测并自动补全缺失的规则。

用法:
  python scripts/ensure_clash_direct.py          # 检测 + 自动修复
  python scripts/ensure_clash_direct.py --check   # 仅检测，不修改
  python scripts/ensure_clash_direct.py --verbose  # 显示详细信息
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ── 需要确保 DIRECT 的域名规则 ────────────────────────────────────
# 格式: (rule_type, payload, target)
REQUIRED_RULES = [
    ("DOMAIN-SUFFIX", "eastmoney.com", "DIRECT"),       # 东方财富 (K线、资金流、筹码等)
    ("DOMAIN-SUFFIX", "sinajs.cn", "DIRECT"),            # 新浪行情 (实时报价)
    ("DOMAIN-SUFFIX", "sina.com.cn", "DIRECT"),          # 新浪财经 (K线备用)
    ("DOMAIN-SUFFIX", "xueqiu.com", "DIRECT"),           # 雪球 (股票数据)
    ("DOMAIN-SUFFIX", "snowballsecurities.com", "DIRECT"),  # 雪球证券
    ("DOMAIN-SUFFIX", "mairui.club", "DIRECT"),          # 麦蕊数据
]

CLASH_CONFIG_PATHS = [
    Path.home() / ".config" / "clash" / "iKuuu_V2.yaml",
    Path.home() / ".config" / "clash" / "config.yaml",
]


def find_clash_configs() -> list[Path]:
    """Find all Clash config files that exist."""
    found = []
    for p in CLASH_CONFIG_PATHS:
        if p.exists():
            found.append(p)
    # Also scan for any other .yaml files in the clash dir
    clash_dir = Path.home() / ".config" / "clash"
    if clash_dir.is_dir():
        for f in clash_dir.glob("*.yaml"):
            if f not in found and f.name not in ("Country.mmdb",):
                found.append(f)
    return found


def parse_rule_line(line: str) -> tuple[str, str, str] | None:
    """Parse a Clash rule line like ' - DOMAIN-SUFFIX,eastmoney.com,DIRECT'."""
    stripped = line.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:]
    elif stripped.startswith("-"):
        stripped = stripped[1:]
    else:
        return None
    parts = stripped.split(",")
    if len(parts) >= 3:
        return (parts[0].strip(), parts[1].strip(), parts[2].strip())
    return None


def check_config(path: Path, verbose: bool = False) -> list[tuple[str, str, str]]:
    """Check which required rules are missing from a config file. Returns missing rules."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  ⚠ 无法读取 {path}: {e}")
        return list(REQUIRED_RULES)

    lines = content.splitlines()
    existing: set[tuple[str, str]] = set()

    in_rules = False
    for line in lines:
        stripped = line.strip()
        if stripped == "rules:":
            in_rules = True
            continue
        if in_rules:
            if stripped and not stripped.startswith("-") and not stripped.startswith("#"):
                break  # Left the rules section
            parsed = parse_rule_line(stripped)
            if parsed:
                # Normalize: match by (type, payload) — ignore target for detection
                existing.add((parsed[0].upper(), parsed[1].lower()))

    missing = []
    for rule_type, payload, target in REQUIRED_RULES:
        key = (rule_type.upper(), payload.lower())
        if key not in existing:
            missing.append((rule_type, payload, target))
        elif verbose:
            print(f"  ✓ {rule_type},{payload},{target}")

    return missing


def fix_config(path: Path, missing: list[tuple[str, str, str]], dry_run: bool = False) -> bool:
    """Insert missing rules at the top of the rules section."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  ⚠ 无法读取 {path}: {e}")
        return False

    lines = content.splitlines(keepends=True)

    # Find the 'rules:' line
    rules_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "rules:":
            rules_idx = i
            break

    if rules_idx is None:
        print(f"  ⚠ 未找到 rules: 段落")
        return False

    # Build the new rule lines to insert
    new_lines = []
    for rule_type, payload, target in missing:
        new_lines.append(f" - {rule_type},{payload},{target}\n")

    if dry_run:
        print(f"  [dry-run] 将在第 {rules_idx + 2} 行插入 {len(new_lines)} 条规则")
        return True

    # Backup before modifying
    backup_path = path.with_suffix(f".{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak")
    shutil.copy2(path, backup_path)

    # Insert after 'rules:' line
    lines[rules_idx + 1:rules_idx + 1] = new_lines
    path.write_text("".join(lines), encoding="utf-8")

    print(f"  ✓ 已备份到 {backup_path.name}")
    return True


def main():
    parser = argparse.ArgumentParser(description="确保 Clash DIRECT 规则存在")
    parser.add_argument("--check", action="store_true", help="仅检测，不修改文件")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")
    parser.add_argument("--config", type=str, help="指定配置文件路径")
    args = parser.parse_args()

    configs = [Path(args.config)] if args.config else find_clash_configs()
    if not configs:
        print("❌ 未找到 Clash 配置文件")
        sys.exit(1)

    all_ok = True
    for cfg in configs:
        print(f"\n📋 {cfg.name} ({cfg})")
        missing = check_config(cfg, verbose=args.verbose)

        if not missing:
            print("  ✅ 所有金融数据源 DIRECT 规则已存在")
            continue

        all_ok = False
        print(f"  ⚠ 缺少 {len(missing)} 条 DIRECT 规则:")
        for rule_type, payload, target in missing:
            print(f"    - {rule_type},{payload},{target}")

        if args.check:
            print("  ℹ 使用 --check 模式，不修改文件")
            continue

        ok = fix_config(cfg, missing)
        if ok:
            print(f"  ✅ 已添加 {len(missing)} 条规则")
        else:
            print("  ❌ 修复失败")

    # Summary
    if all_ok:
        print("\n✅ 所有配置文件的 DIRECT 规则完整")
    elif not args.check:
        print("\n✅ 已修复缺失的 DIRECT 规则，请重启 Clash 或重新加载配置使其生效")
    else:
        print("\n⚠ 存在缺失规则，运行不带 --check 参数可自动修复")
        sys.exit(1)


if __name__ == "__main__":
    main()
