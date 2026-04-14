"""自动重训管线 — trigger → retrain → validate → gate → promote/rollback"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RetrainConfig:
    universe: str = "csi2000"
    model_dir: str = ""
    archive_dir: str = "models/_archive"
    ic_threshold: float = 0.10
    ic_improvement_min: float = -0.005
    psi_threshold: float = 0.20
    max_age_days: int = 90
    enable_incremental: bool = False
    project_root: str = ""

    def model_dir_path(self) -> Path:
        root = Path(self.project_root) if self.project_root else Path.cwd()
        return root / self.model_dir

    def archive_dir_path(self) -> Path:
        root = Path(self.project_root) if self.project_root else Path.cwd()
        return root / self.archive_dir


@dataclass
class PipelineResult:
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
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @property
    def summary(self) -> str:
        if self.error:
            return f"ERROR: {self.error}"
        if not self.triggered:
            return "SKIP: no trigger fired"
        if self.promoted:
            return f"PROMOTED: IC {self.old_ic:.4f}→{self.new_ic:.4f} (Δ={self.ic_delta:+.4f})"
        if self.rolled_back:
            return f"ROLLBACK: IC {self.new_ic:.4f} below gate"
        return f"RETRAINED: exit={self.retrain_exit_code}"


class RetrainTrigger:
    """重训触发条件检测"""

    def __init__(self, config: RetrainConfig) -> None:
        self.config = config

    def evaluate(self, current_ic: float, model_age_days: int, psi: float) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if current_ic < self.config.ic_threshold:
            reasons.append(f"IC {current_ic:.4f} < threshold {self.config.ic_threshold}")
        if model_age_days > self.config.max_age_days:
            reasons.append(f"Model age {model_age_days}d > max {self.config.max_age_days}d")
        if psi > self.config.psi_threshold:
            reasons.append(f"PSI {psi:.4f} > threshold {self.config.psi_threshold}")
        return len(reasons) > 0, reasons


class AutoRetrainPipeline:
    """自动重训管线编排器

    闭环: trigger检测 → 归档旧模型 → 执行重训 → IC质量门控 → promote/rollback
    """

    def __init__(self, config: RetrainConfig) -> None:
        self.config = config
        self._trigger = RetrainTrigger(config)

    def run(
        self,
        current_ic: float = 0.0,
        model_age_days: int = 0,
        psi: float = 0.0,
        retrain_fn: Any = None,
        evaluate_fn: Any = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> PipelineResult:
        t0 = time.time()
        result = PipelineResult(timestamp=datetime.now().isoformat())

        try:
            # 1. Trigger 检测
            if force:
                result.triggered = True
                result.trigger_reasons = ["force=True"]
            else:
                result.triggered, result.trigger_reasons = self._trigger.evaluate(
                    current_ic, model_age_days, psi
                )

            if not result.triggered:
                result.duration_seconds = time.time() - t0
                return result

            if dry_run:
                result.duration_seconds = time.time() - t0
                return result

            result.old_ic = current_ic

            # 2. 归档旧模型
            self._archive_model()

            # 3. 执行重训
            if retrain_fn is not None:
                retrain_result = retrain_fn()
                result.retrained = True
                result.retrain_exit_code = retrain_result.get("exit_code", 0)
            else:
                logger.warning("No retrain_fn provided, skipping retrain")
                result.retrained = False
                result.duration_seconds = time.time() - t0
                return result

            # 4. 评估新模型
            if evaluate_fn is not None:
                eval_result = evaluate_fn()
                result.new_ic = eval_result.get("ic", 0.0)
            result.ic_delta = result.new_ic - result.old_ic

            # 5. 质量门控
            result.gate_passed = (
                result.new_ic >= self.config.ic_threshold
                and result.ic_delta >= self.config.ic_improvement_min
            )

            if result.gate_passed:
                result.promoted = True
                logger.info(f"Model PROMOTED: {result.summary}")
            else:
                self._rollback_model()
                result.rolled_back = True
                logger.warning(f"Model ROLLED BACK: {result.summary}")

        except Exception as e:
            result.error = str(e)
            logger.exception("Retrain pipeline error")

        result.duration_seconds = time.time() - t0
        return result

    def _archive_model(self) -> None:
        model_dir = self.config.model_dir_path()
        if not model_dir.exists():
            return
        archive = self.config.archive_dir_path()
        archive.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = archive / f"model_{stamp}"
        if model_dir.is_dir():
            shutil.copytree(model_dir, dest, dirs_exist_ok=True)
        logger.info(f"Archived model to {dest}")

    def _rollback_model(self) -> None:
        archive = self.config.archive_dir_path()
        if not archive.exists():
            return
        archives = sorted(archive.iterdir(), reverse=True)
        if not archives:
            return
        latest = archives[0]
        model_dir = self.config.model_dir_path()
        if latest.is_dir():
            shutil.copytree(latest, model_dir, dirs_exist_ok=True)
        logger.info(f"Rolled back to {latest}")
