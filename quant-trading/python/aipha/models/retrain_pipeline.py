"""自动重训管线编排器 — trigger → retrain → validate → gate → promote/rollback

将 RetrainTrigger 决策、滚动重训脚本、IC 验证、质量门控串为闭环。
支持 csi1000 / csi2000 两个 universe，可通过 daily_scheduler Task-E 调度。

典型用法::

    from aipha.models.retrain_pipeline import AutoRetrainPipeline, RetrainConfig

    cfg = RetrainConfig.from_yaml("configs/retrain_csi2000.yml")
    pipeline = AutoRetrainPipeline(cfg)
    result = pipeline.run()
    if result.promoted:
        print(f"新模型已上线: IC={result.new_ic:.4f}")
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)


# ── 数据类 ──────────────────────────────────────────────────

@dataclass
class RetrainConfig:
    """重训管线配置"""

    universe: str = "csi2000"
    model_dir: str = ""
    archive_dir: str = "models/_archive"
    ic_threshold: float = 0.10
    ic_improvement_min: float = -0.005
    psi_threshold: float = 0.20
    max_age_days: int = 90
    enable_incremental: bool = False
    legacy_ml_compat: bool = False
    project_root: str = ""

    @classmethod
    def from_yaml(cls, path: str | Path) -> RetrainConfig:
        """从 YAML 配置文件加载"""
        import yaml

        path = Path(path)
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        # 支持顶层 retrain: 节 或扁平结构
        cfg = raw.get("retrain", raw)
        proj = str(path.resolve().parent.parent)
        return cls(
            universe=cfg.get("universe", "csi2000"),
            model_dir=cfg.get("model_dir", ""),
            archive_dir=cfg.get("archive_dir", "models/_archive"),
            ic_threshold=cfg.get("triggers", {}).get("ic_threshold", 0.10),
            ic_improvement_min=cfg.get("triggers", {}).get(
                "ic_improvement_min", -0.005
            ),
            psi_threshold=cfg.get("triggers", {}).get("psi_threshold", 0.20),
            max_age_days=cfg.get("triggers", {}).get("max_age_days", 90),
            enable_incremental=cfg.get("enable_incremental", False),
            legacy_ml_compat=cfg.get("legacy_ml_compat", False) is True,
            project_root=proj,
        )

    def model_dir_path(self) -> Path:
        """返回模型目录绝对路径"""
        root = Path(self.project_root) if self.project_root else Path.cwd()
        return root / self.model_dir

    def archive_dir_path(self) -> Path:
        """返回归档目录绝对路径"""
        root = Path(self.project_root) if self.project_root else Path.cwd()
        return root / self.archive_dir


@dataclass
class PipelineResult:
    """重训管线执行结果"""

    triggered: bool = False
    trigger_reasons: list[str] = field(default_factory=list)
    retrained: bool = False
    retrain_exit_code: int = -1
    old_ic: float = 0.0
    new_ic: float = 0.0
    ic_delta: float = 0.0
    gate_passed: bool = False
    promoted: bool = False
    rolled_back: bool = False
    error: str = ""
    timestamp: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "triggered": self.triggered,
            "trigger_reasons": self.trigger_reasons,
            "retrained": self.retrained,
            "retrain_exit_code": self.retrain_exit_code,
            "old_ic": self.old_ic,
            "new_ic": self.new_ic,
            "ic_delta": self.ic_delta,
            "gate_passed": self.gate_passed,
            "promoted": self.promoted,
            "rolled_back": self.rolled_back,
            "error": self.error,
            "timestamp": self.timestamp,
            "duration_seconds": self.duration_seconds,
        }

    @property
    def summary(self) -> str:
        """一行摘要"""
        if self.error:
            return f"ERROR: {self.error}"
        if not self.triggered:
            return "SKIP: no trigger fired"
        if self.promoted:
            return (
                f"PROMOTED: IC {self.old_ic:.4f}→{self.new_ic:.4f} "
                f"(Δ={self.ic_delta:+.4f})"
            )
        if self.rolled_back:
            return (
                f"ROLLBACK: IC {self.new_ic:.4f} < gate "
                f"({self.config.ic_threshold if hasattr(self, 'config') else '?'})"
            )
        return f"RETRAINED: exit={self.retrain_exit_code}"


# ── 管线主类 ────────────────────────────────────────────────

class AutoRetrainPipeline:
    """自动重训管线编排器

    闭环流程:
        1. Trigger 检测（RetrainTrigger.evaluate()）
        2. 归档旧模型
        3. 执行重训（subprocess → retrain_csi*_rolling.py --force）
        4. IC 质量门控
        5. 通过 → promote（保留新模型）
           失败 → rollback（恢复旧模型）
        6. 写审计日志
    """

    def __init__(self, config: RetrainConfig) -> None:
        self.config = config
        self._project_root = Path(
            config.project_root or PROJECT_ROOT
        )

    def run(
        self,
        end_date: str | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> PipelineResult:
        """执行完整重训管线

        Args:
            end_date: 训练截止日（默认今天）
            force: 跳过 trigger 检查强制重训
            dry_run: 仅检查 trigger，不执行重训

        Returns:
            PipelineResult 执行结果
        """
        import time

        t0 = time.time()
        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        tag = datetime.now().strftime("v%Y%m%d")
        result = PipelineResult(timestamp=datetime.now().isoformat())
        universe = self.config.universe

        if not self.config.legacy_ml_compat:
            raise RuntimeError(
                "legacy automatic ML retraining is disabled; migrate to the canonical "
                "aipha.research train_tushare pipeline or explicitly set "
                "legacy_ml_compat: true for a historical model"
            )

        logger.info(
            f"=== AutoRetrain [{universe}] start "
            f"(end={end_date}, force={force}) ==="
        )

        # ── Step 1: Trigger 检测 ──
        if force:
            result.triggered = True
            result.trigger_reasons = ["force"]
        else:
            triggered, reasons, details = self._check_trigger()
            result.triggered = triggered
            result.trigger_reasons = reasons
            logger.info(
                f"Trigger: {triggered}, reasons={reasons}"
            )

        if not result.triggered:
            result.duration_seconds = time.time() - t0
            self._write_audit(result)
            logger.info("=== 无需重训 ===")
            return result

        if dry_run:
            logger.info("=== dry-run: 检测到触发但不执行 ===")
            result.duration_seconds = time.time() - t0
            return result

        # ── Step 2: 归档旧模型 ──
        old_report = self._load_evaluation()
        archive_path = self._archive_model(tag)

        # ── Step 3: 执行重训 ──
        rc = self._run_retrain(end_date)
        result.retrained = True
        result.retrain_exit_code = rc
        if rc != 0:
            result.error = f"retrain failed (exit={rc})"
            if archive_path:
                self._rollback(archive_path)
                result.rolled_back = True
            result.duration_seconds = time.time() - t0
            self._write_audit(result)
            return result

        # ── Step 4: 质量门控 ──
        new_report = self._load_evaluation()
        passed, old_ic, new_ic = self._quality_gate(old_report, new_report)
        result.old_ic = old_ic
        result.new_ic = new_ic
        result.ic_delta = new_ic - old_ic
        result.gate_passed = passed

        if passed:
            result.promoted = True
            logger.info(
                f"✅ Gate PASS: IC {old_ic:.4f}→{new_ic:.4f}, "
                f"模型已更新"
            )
        else:
            logger.warning(
                f"❌ Gate FAIL: IC {new_ic:.4f} "
                f"(threshold={self.config.ic_threshold})"
            )
            if archive_path:
                self._rollback(archive_path)
                result.rolled_back = True

        result.duration_seconds = time.time() - t0
        self._write_audit(result)
        logger.info(f"=== AutoRetrain [{universe}] done: {result.summary} ===")
        return result

    def _check_trigger(self) -> tuple[bool, list[str], dict]:
        """使用 RetrainTrigger 评估是否需要重训

        Returns:
            (should_retrain, reason_names, details_dict)
        """
        try:
            from aipha.monitoring.retrain_trigger import RetrainTrigger
        except ImportError:
            logger.warning("RetrainTrigger 不可用，降级为 age-based 检查")
            return self._fallback_age_check()

        trigger = RetrainTrigger(psi_threshold=self.config.psi_threshold)

        # 收集可用的漂移/衰减信息
        drift_report = None
        decay_snapshot = None
        try:
            from aipha.models.drift_monitor import DriftMonitor

            model_dir = self.config.model_dir_path()
            pred_path = model_dir / "oos_predictions.parquet"
            meta_path = model_dir / "meta.pkl"
            if pred_path.exists() and meta_path.exists():
                import pickle
                import pandas as pd

                with open(meta_path, "rb") as f:
                    meta = pickle.load(f)
                cols = [
                    c for c in ["pred_score"]
                    if c in (meta.get("feature_cols", []) + ["pred_score"])
                ]
                if cols:
                    df = pd.read_parquet(pred_path)
                    if "trade_date" in df.columns and len(df) > 120:
                        df = df.sort_values("trade_date")
                        dates = df["trade_date"].unique()
                        cutoff = dates[-60]
                        monitor = DriftMonitor(
                            psi_threshold=self.config.psi_threshold
                        )
                        avail = [c for c in cols if c in df.columns]
                        if avail:
                            monitor.set_baseline(
                                df[df["trade_date"] < cutoff][avail]
                            )
                            drift_report = monitor.detect_drift(
                                df[df["trade_date"] >= cutoff][avail]
                            )
        except Exception as e:
            logger.warning(f"漂移检测数据加载失败: {e}")

        decision = trigger.evaluate(
            drift_report=drift_report,
            decay_snapshot=decay_snapshot,
        )
        reason_names = [r.value for r in decision.reasons]
        return decision.should_retrain, reason_names, decision.details

    def _fallback_age_check(self) -> tuple[bool, list[str], dict]:
        """降级: 仅检查模型年龄"""
        meta_path = self.config.model_dir_path() / "meta.pkl"
        if not meta_path.exists():
            return True, ["no_model"], {"reason": "首次训练"}
        age_days = (
            datetime.now() - datetime.fromtimestamp(meta_path.stat().st_mtime)
        ).days
        if age_days > self.config.max_age_days:
            return (
                True,
                ["age_exceeded"],
                {"age_days": age_days, "max": self.config.max_age_days},
            )
        return False, [], {"age_days": age_days}

    def _archive_model(self, tag: str) -> Path | None:
        """归档当前模型"""
        model_dir = self.config.model_dir_path()
        if not model_dir.exists():
            return None
        archive_dir = self.config.archive_dir_path()
        archive_dir.mkdir(parents=True, exist_ok=True)
        universe = self.config.universe
        dst = archive_dir / f"ml_{universe}_lgb_fund_{tag}"
        if dst.exists():
            logger.warning(f"归档目标已存在: {dst}")
            return dst
        shutil.copytree(model_dir, dst)
        logger.info(f"模型已归档: {model_dir.name} → {dst.name}")
        return dst

    def _load_evaluation(self) -> dict | None:
        """加载 evaluation_report.json"""
        report_path = self.config.model_dir_path() / "evaluation_report.json"
        if not report_path.exists():
            return None
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _run_retrain(self, end_date: str) -> int:
        """执行重训子进程

        调用对应 universe 的 retrain_csi*_rolling.py --force --skip-archive
        """
        universe = self.config.universe
        script_name = f"retrain_{universe}_rolling.py"
        script_path = self._project_root / "scripts" / script_name
        if not script_path.exists():
            logger.error(f"重训脚本不存在: {script_path}")
            return 1
        cmd = [
            sys.executable,
            str(script_path),
            "--force",
            "--skip-archive",  # 归档已由 pipeline 完成
            "--end-date", end_date,
        ]
        if self.config.enable_incremental:
            cmd.append("--enable-incremental")
        logger.info(f"执行重训: {script_name} --end-date {end_date}")
        result = subprocess.run(cmd, capture_output=False)
        return result.returncode

    def _quality_gate(
        self, old_report: dict | None, new_report: dict | None
    ) -> tuple[bool, float, float]:
        """IC 质量门控

        Returns:
            (passed, old_ic, new_ic)
        """
        if new_report is None:
            return False, 0.0, 0.0

        new_ic = new_report.get("mean_ic", 0.0)
        if isinstance(new_ic, dict):
            new_ic = new_report.get("summary", {}).get("mean_ic", 0.0)

        old_ic = 0.0
        if old_report:
            old_ic = old_report.get("mean_ic", 0.0)
            if isinstance(old_ic, dict):
                old_ic = old_report.get("summary", {}).get("mean_ic", 0.0)

        delta = new_ic - old_ic
        # Gate: 新 IC 须达标，且不低于旧 IC 太多
        passed = (
            new_ic >= self.config.ic_threshold
            and delta >= self.config.ic_improvement_min
        )
        return passed, old_ic, new_ic

    def _rollback(self, archive_path: Path) -> bool:
        """回滚到旧模型

        Args:
            archive_path: 归档的旧模型路径

        Returns:
            True 如果回滚成功
        """
        if archive_path is None or not archive_path.exists():
            logger.error("无归档可回滚")
            return False
        model_dir = self.config.model_dir_path()
        try:
            if model_dir.exists():
                shutil.rmtree(model_dir)
            shutil.copytree(archive_path, model_dir)
            logger.info(f"已回滚: {archive_path.name} → {model_dir.name}")
            return True
        except Exception as e:
            logger.error(f"回滚失败: {e}")
            return False

    def _write_audit(self, result: PipelineResult) -> None:
        """写审计日志到 logs/audit/"""
        audit_dir = self._project_root / "logs" / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        audit_file = audit_dir / f"retrain_{self.config.universe}_{ts}.json"
        with open(audit_file, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"审计日志: {audit_file.name}")
