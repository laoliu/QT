#!/usr/bin/env python
"""CSI2000 滚动重训管线 — O4 优化

自动化流程：
1. 数据覆盖检查（equity cache 是否覆盖到最新月份）
2. 特征漂移检测（PSI > 0.2 触发告警）
3. 滚动 Walk-Forward 重训（扩展训练窗口，新增预测折）
4. IC 对比评估（新模型 vs 旧模型）
5. 版本归档（旧模型备份，新模型替换）
6. 回测验证（可选，使用 risk_controlled_replay 快速验证）

用法:
    # 标准滚动重训
    python scripts/retrain_csi2000_rolling.py

    # 强制重训（忽略漂移检查）
    python scripts/retrain_csi2000_rolling.py --force

    # 仅检查漂移，不执行重训
    python scripts/retrain_csi2000_rolling.py --check-only

    # 指定终止日期
    python scripts/retrain_csi2000_rolling.py --end-date 2026-03-14
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 确保 src 在路径中
_project_root = Path(__file__).resolve().parent.parent
_src = _project_root / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("retrain_csi2000")

# ── 配置常量 ─────────────────────────────────────────────────
MODEL_DIR = _project_root / "models" / "ml_csi2000_lgb_fund"
ARCHIVE_DIR = _project_root / "models" / "_archive"
DATA_CACHE = _project_root / "data" / "cache" / "equity_tushare"
TRAIN_START = "2017-01-03"
TRAIN_YEARS = 3
PREDICT_MONTHS = 3
FORWARD_DAYS = 5
MODEL_TYPE = "lightgbm"
UNIVERSE = "csi2000"
IC_THRESHOLD = 0.10  # 新模型 IC 须 >= 此值
IC_IMPROVEMENT_MIN = -0.005  # 允许微降 0.5%
PSI_THRESHOLD = 0.20  # PSI 超此值视为显著漂移


def parse_args() -> argparse.Namespace:
    """CLI 参数解析"""
    p = argparse.ArgumentParser(description="CSI2000 滚动重训")
    p.add_argument(
        "--end-date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="训练截止日（默认今天）",
    )
    p.add_argument(
        "--force", action="store_true", help="跳过漂移检查强制重训",
    )
    p.add_argument(
        "--check-only", action="store_true", help="仅检查漂移不重训",
    )
    p.add_argument(
        "--skip-archive", action="store_true", help="跳过旧模型归档",
    )
    p.add_argument(
        "--enable-incremental", action="store_true",
        help="启用 WF 折间增量训练 (warm-start)",
    )
    return p.parse_args()


def check_data_coverage(end_date: str) -> dict:
    """检查 equity 数据缓存覆盖度

    Returns:
        {"covered": bool, "latest_file": str, "latest_month": str,
         "target_month": str, "missing_months": list}
    """
    target_month = end_date[:7].replace("-", "")  # "202603"

    # 扫描 data/cache/equity_tushare/ 中的月度分片
    if not DATA_CACHE.exists():
        return {
            "covered": False,
            "latest_file": "",
            "latest_month": "",
            "target_month": target_month,
            "missing_months": [target_month],
        }

    parquet_files = sorted(DATA_CACHE.glob("*.parquet"))
    if not parquet_files:
        return {
            "covered": False,
            "latest_file": "",
            "latest_month": "",
            "target_month": target_month,
            "missing_months": [target_month],
        }

    latest = parquet_files[-1].stem  # e.g. "daily_202602"
    # 提取月份后缀
    parts = latest.split("_")
    latest_month = parts[-1] if parts else ""

    covered = latest_month >= target_month
    return {
        "covered": covered,
        "latest_file": str(parquet_files[-1].name),
        "latest_month": latest_month,
        "target_month": target_month,
        "missing_months": [] if covered else [target_month],
    }


def check_feature_drift() -> dict:
    """检测特征漂移（PSI）

    Returns:
        {"has_drift": bool, "psi_values": dict, "drifted_features": list}
    """
    try:
        from aipha.models.drift_monitor import DriftMonitor

        # 需要 OOS 预测文件中的特征列来检测漂移
        pred_path = MODEL_DIR / "oos_predictions.parquet"
        meta_path = MODEL_DIR / "meta.pkl"
        if not pred_path.exists() or not meta_path.exists():
            logger.warning("缺少 oos_predictions.parquet 或 meta.pkl，跳过漂移检测")
            return {"has_drift": False, "psi_values": {}, "drifted_features": []}

        import pickle

        import pandas as pd

        with open(meta_path, "rb") as f:
            meta = pickle.load(f)

        feature_cols = meta.get("feature_cols", [])
        if not feature_cols:
            logger.warning("meta.pkl 中无 feature_cols，跳过漂移检测")
            return {"has_drift": False, "psi_values": {}, "drifted_features": []}

        # 加载预测数据，取最近 6 个月 vs 更早作为基线/测试
        df = pd.read_parquet(pred_path)
        if "trade_date" not in df.columns:
            return {"has_drift": False, "psi_values": {}, "drifted_features": []}

        # 按日期排序
        df = df.sort_values("trade_date")
        dates = df["trade_date"].unique()
        if len(dates) < 120:
            return {"has_drift": False, "psi_values": {}, "drifted_features": []}

        cutoff = dates[-60]  # 最近 60 天
        baseline_df = df[df["trade_date"] < cutoff]
        recent_df = df[df["trade_date"] >= cutoff]

        # 只检测 pred_score 的 PSI（特征不在 OOS 文件中）
        monitor = DriftMonitor(psi_threshold=PSI_THRESHOLD)
        available_cols = [c for c in ["pred_score"] if c in df.columns]
        if not available_cols:
            return {"has_drift": False, "psi_values": {}, "drifted_features": []}

        monitor.set_baseline(baseline_df[available_cols])
        report = monitor.detect_drift(recent_df[available_cols])

        return {
            "has_drift": report.has_significant_drift,
            "psi_values": {f.feature: f.psi for f in report.features},
            "drifted_features": report.drifted_features,
        }
    except Exception as e:
        logger.error(f"漂移检测失败: {e}")
        return {"has_drift": False, "psi_values": {}, "drifted_features": []}


def load_existing_evaluation() -> dict | None:
    """加载现有模型评估报告"""
    report_path = MODEL_DIR / "evaluation_report.json"
    if not report_path.exists():
        logger.warning(f"评估报告不存在: {report_path}")
        return None
    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)


def archive_model(tag: str) -> Path:
    """归档当前模型到 _archive/

    Args:
        tag: 版本标签（如 "v20260315"）

    Returns:
        归档目录路径
    """
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dst = ARCHIVE_DIR / f"ml_csi2000_lgb_fund_{tag}"
    if dst.exists():
        logger.warning(f"归档目标已存在，跳过: {dst}")
        return dst
    if MODEL_DIR.exists():
        shutil.copytree(MODEL_DIR, dst)
        logger.info(f"模型已归档: {MODEL_DIR.name} → {dst.name}")
    else:
        logger.warning(f"源模型目录不存在: {MODEL_DIR}")
    return dst


def run_retrain(end_date: str, enable_incremental: bool = False) -> int:
    """执行重训（调用 train_ml_factor.py）

    Returns:
        子进程返回码（0 = 成功）
    """
    cmd = [
        sys.executable,
        str(_project_root / "scripts" / "train_ml_factor.py"),
        "--universe", UNIVERSE,
        "--model", MODEL_TYPE,
        "--start", TRAIN_START,
        "--end", end_date,
        "--train-years", str(TRAIN_YEARS),
        "--predict-months", str(PREDICT_MONTHS),
        "--forward-days", str(FORWARD_DAYS),
        "--deep-fundamental",
        "--output", str(MODEL_DIR),
    ]
    if enable_incremental:
        cmd.append("--enable-incremental")
    logger.info(f"执行重训: {' '.join(cmd[-8:])}")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def compare_ic(old_report: dict, new_report: dict) -> dict:
    """新旧模型 IC 对比

    Returns:
        {"old_ic": float, "new_ic": float, "delta": float,
         "improved": bool, "acceptable": bool}
    """
    old_ic = old_report.get("mean_ic", 0.0)
    new_ic = new_report.get("mean_ic", 0.0)

    # 如果报告嵌套在 summary 中
    if isinstance(old_ic, dict):
        old_ic = old_report.get("summary", {}).get("mean_ic", 0.0)
    if isinstance(new_ic, dict):
        new_ic = new_report.get("summary", {}).get("mean_ic", 0.0)

    delta = new_ic - old_ic
    improved = delta > 0
    acceptable = new_ic >= IC_THRESHOLD and delta >= IC_IMPROVEMENT_MIN

    return {
        "old_ic": old_ic,
        "new_ic": new_ic,
        "delta": delta,
        "improved": improved,
        "acceptable": acceptable,
    }


def main() -> None:
    """主流程：检查→漂移→归档→重训→对比→输出"""
    args = parse_args()
    end_date = args.end_date
    tag = datetime.now().strftime("v%Y%m%d")

    logger.info(f"=== CSI2000 滚动重训管线 start (end_date={end_date}) ===")

    # Step 1: 数据覆盖检查
    coverage = check_data_coverage(end_date)
    logger.info(
        f"数据覆盖: latest={coverage['latest_month']}, "
        f"target={coverage['target_month']}, covered={coverage['covered']}"
    )
    if not coverage["covered"]:
        logger.warning(
            f"数据不完整，缺少: {coverage['missing_months']}。"
            "请先运行数据更新脚本补全数据。"
        )
        if not args.force:
            logger.error("中止。使用 --force 可跳过数据检查。")
            sys.exit(1)

    # Step 2: 特征漂移检测
    drift = check_feature_drift()
    if drift["has_drift"]:
        logger.warning(
            f"检测到显著漂移! PSI: {drift['psi_values']}, "
            f"漂移特征: {drift['drifted_features']}"
        )
    else:
        logger.info("未检测到显著漂移")

    if args.check_only:
        logger.info("--check-only 模式，检查完毕")
        return

    # 决策：是否重训
    need_retrain = args.force or drift["has_drift"]
    if not need_retrain:
        # 检查上次训练时间（meta.pkl 修改时间）
        meta_path = MODEL_DIR / "meta.pkl"
        if meta_path.exists():
            age_days = (
                datetime.now() - datetime.fromtimestamp(meta_path.stat().st_mtime)
            ).days
            if age_days > 90:
                logger.info(f"模型已 {age_days} 天未更新，触发定期重训")
                need_retrain = True
            else:
                logger.info(f"模型 {age_days} 天前更新，无需重训")
        else:
            logger.info("无现有模型，执行首次训练")
            need_retrain = True

    if not need_retrain:
        logger.info("=== 无需重训，流程结束 ===")
        return

    # Step 3: 归档旧模型
    old_report = load_existing_evaluation()
    if not args.skip_archive and MODEL_DIR.exists():
        archive_path = archive_model(tag)
        logger.info(f"旧模型已归档: {archive_path}")

    # Step 4: 执行重训
    logger.info(f"开始重训: {TRAIN_START} ~ {end_date}, {MODEL_TYPE}")
    rc = run_retrain(end_date, enable_incremental=args.enable_incremental)
    if rc != 0:
        logger.error(f"重训失败 (exit code={rc})")
        sys.exit(rc)
    logger.info("重训成功完成")

    # Step 5: IC 对比
    new_report = load_existing_evaluation()
    if old_report and new_report:
        comparison = compare_ic(old_report, new_report)
        logger.info(
            f"IC 对比: old={comparison['old_ic']:.4f} → "
            f"new={comparison['new_ic']:.4f} "
            f"(Δ={comparison['delta']:+.4f})"
        )
        if not comparison["acceptable"]:
            logger.warning(
                f"新模型 IC 不及格 (IC={comparison['new_ic']:.4f} "
                f"< {IC_THRESHOLD})"
            )
        elif comparison["improved"]:
            logger.info("✅ 新模型 IC 有提升，接受更新")
        else:
            logger.info("⚠️ 新模型 IC 微降但可接受")
    elif new_report:
        new_ic = new_report.get("mean_ic", 0.0)
        if isinstance(new_ic, dict):
            new_ic = new_report.get("summary", {}).get("mean_ic", 0.0)
        logger.info(f"首次训练 IC={new_ic:.4f}")

    logger.info(f"=== CSI2000 滚动重训管线完成 (tag={tag}) ===")


if __name__ == "__main__":
    main()
