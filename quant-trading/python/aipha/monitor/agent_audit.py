"""
Agent 操作审计链路（AgentAudit）

记录 AI 代理在策略研发、模型训练、回测触发过程中的所有操作，
提供完整的可追溯审计链路，符合知识库 T 章节"完整审计链路"要求。

功能：
- 记录因子生成 trace（prompt hash / 生成代码 / 源模型版本）
- 记录回测触发 trace（策略 ID / 配置 hash / 触发来源）
- 记录模型更新 trace（模型 ID / 新旧版本 / 性能对比）
- 记录代码修改 trace（文件路径 / diff / 相关策略）
- 查询审计轨迹（按策略 ID / 时间范围 / 事件类型）
- 可选集成 MLflow Tracing（mlflow.start_span），降级为 JSONL 文件存储

使用示例::

    audit = AgentAudit()
    tid = audit.log_factor_generation(
        factor_name="ep_ratio",
        prompt_hash="abc123",
        code="def ep_ratio(df): ...",
    )
    records = audit.query_audit_trail(strategy_id="double_low")
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

# ── 默认审计日志路径 ────────────────────────────────────────────────────
_DEFAULT_LOG_DIR = PROJECT_ROOT / "logs" / "audit"


class AuditEventType(str, Enum):
    """审计事件类型枚举"""
    FACTOR_GENERATION = "factor_generation"
    BACKTEST_TRIGGER   = "backtest_trigger"
    MODEL_UPDATE       = "model_update"
    CODE_MODIFICATION  = "code_modification"
    STRATEGY_DEPLOY    = "strategy_deploy"
    COMPLIANCE_CHECK   = "compliance_check"
    CUSTOM             = "custom"


@dataclass
class AuditRecord:
    """单条审计记录"""
    trace_id: str
    event_type: str
    timestamp: str                        # ISO 8601
    strategy_id: Optional[str]
    model_version: Optional[str]
    triggered_by: str                     # "agent" / "user" / "system"
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    parent_trace_id: Optional[str] = None
    mlflow_run_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可存储的字典。"""
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AuditRecord":
        """从字典反序列化。"""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _make_trace_id() -> str:
    """生成 trace_id：uuid4 前 8 位 + 时间戳后 6 位（可读性强）。"""
    ts = datetime.now().strftime("%f")[-6:]
    uid = str(uuid.uuid4()).replace("-", "")[:8]
    return f"tr_{uid}_{ts}"


def _safe_hash(text: str) -> str:
    """对字符串计算 SHA-256 前 12 位 hex 摘要。"""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class AgentAudit:
    """Agent 操作审计链路

    Args:
        log_dir: 审计日志 JSONL 目录，默认 ``logs/audit/``
        model_version: 当前 AI Agent 模型版本（如 ``claude-3-5-sonnet``）
        enable_mlflow: 若 True 且 mlflow 已安装，同步写入 MLflow Tracing
    """

    def __init__(
        self,
        log_dir: Path | str | None = None,
        model_version: str = "unknown",
        enable_mlflow: bool = False,
    ) -> None:
        self._log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._model_version = model_version
        self._mlflow_enabled = False

        if enable_mlflow:
            try:
                import mlflow  # type: ignore[import-untyped]  # noqa: F401
                self._mlflow_enabled = True
            except ImportError:
                logger.debug("mlflow 未安装，AgentAudit 降级为纯 JSONL 存储")

    # ── 核心写入 ──────────────────────────────────────────────────────

    def _log_event(
        self,
        event_type: AuditEventType,
        *,
        strategy_id: str | None = None,
        summary: str,
        details: dict[str, Any] | None = None,
        triggered_by: str = "agent",
        parent_trace_id: str | None = None,
    ) -> str:
        """记录一条审计事件，写入 JSONL 文件并可选写入 MLflow。

        Returns:
            trace_id — 本次事件的唯一标识符，供调用方关联回测结果。
        """
        trace_id = _make_trace_id()
        now = datetime.now().isoformat(timespec="seconds")
        rec = AuditRecord(
            trace_id=trace_id,
            event_type=event_type.value,
            timestamp=now,
            strategy_id=strategy_id,
            model_version=self._model_version,
            triggered_by=triggered_by,
            summary=summary,
            details=details or {},
            parent_trace_id=parent_trace_id,
        )

        # 写 JSONL（按日期分文件）
        fname = f"agent_audit_{datetime.now():%Y%m%d}.jsonl"
        fpath = self._log_dir / fname
        try:
            with fpath.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("AgentAudit 写入失败: %s", exc)

        # 可选：MLflow Tracing
        if self._mlflow_enabled:
            self._emit_mlflow_span(rec)

        logger.debug("AgentAudit [%s] %s  trace_id=%s",
                     event_type.value, summary[:60], trace_id)
        return trace_id

    def _emit_mlflow_span(self, rec: AuditRecord) -> None:
        """向 MLflow 写入 audit span（失败时静默忽略）。"""
        try:
            import mlflow  # type: ignore[import-untyped]
            with mlflow.start_span(name=rec.event_type) as span:
                span.set_attribute("trace_id", rec.trace_id)
                span.set_attribute("strategy_id", rec.strategy_id or "")
                span.set_attribute("triggered_by", rec.triggered_by)
                span.set_inputs({"summary": rec.summary})
        except Exception:
            pass

    # ── 公开 log_* 方法 ──────────────────────────────────────────────

    def log_factor_generation(
        self,
        factor_name: str,
        prompt_hash: str,
        code: str,
        *,
        strategy_id: str | None = None,
        parent_trace_id: str | None = None,
    ) -> str:
        """记录因子生成审计事件。

        Args:
            factor_name: 因子名称（如 ``ep_ratio``）
            prompt_hash: 生成该代码的 prompt 前 12 字符 hash（外部传入或调用 ``_safe_hash``）
            code: 生成的因子代码字符串（完整）
            strategy_id: 关联策略 ID（可选）
            parent_trace_id: 若本次生成依赖父任务，填入父 trace_id

        Returns:
            本次事件的 trace_id
        """
        code_hash = _safe_hash(code)
        return self._log_event(
            AuditEventType.FACTOR_GENERATION,
            strategy_id=strategy_id,
            summary=f"因子生成: {factor_name} (prompt_hash={prompt_hash[:8]}, code_hash={code_hash})",
            details={
                "factor_name": factor_name,
                "prompt_hash": prompt_hash,
                "code_hash": code_hash,
                "code_lines": len(code.splitlines()),
            },
            parent_trace_id=parent_trace_id,
        )

    def log_backtest_trigger(
        self,
        strategy_id: str,
        config_hash: str,
        triggered_by: str = "agent",
        *,
        config_path: str | None = None,
        parent_trace_id: str | None = None,
    ) -> str:
        """记录回测触发审计事件。

        Args:
            strategy_id: 策略 ID 或名称
            config_hash: 回测配置文件 hash（可由调用方提前计算）
            triggered_by: 触发来源（``agent`` / ``user`` / ``sweep``）
            config_path: 配置文件路径（可选）
            parent_trace_id: 父 trace_id

        Returns:
            本次事件的 trace_id
        """
        return self._log_event(
            AuditEventType.BACKTEST_TRIGGER,
            strategy_id=strategy_id,
            summary=f"回测触发: {strategy_id} (config_hash={config_hash[:8]}, by={triggered_by})",
            details={
                "strategy_id": strategy_id,
                "config_hash": config_hash,
                "config_path": config_path or "",
            },
            triggered_by=triggered_by,
            parent_trace_id=parent_trace_id,
        )

    def log_model_update(
        self,
        model_id: str,
        old_version: str,
        new_version: str,
        *,
        strategy_id: str | None = None,
        metrics_delta: dict[str, float] | None = None,
        parent_trace_id: str | None = None,
    ) -> str:
        """记录模型更新审计事件。

        Args:
            model_id: 模型标识符（如 ``lgb_csi2000_20d``）
            old_version: 旧版本 tag/hash
            new_version: 新版本 tag/hash
            strategy_id: 关联策略 ID（可选）
            metrics_delta: 新旧版本性能差值字典（如 ``{"ic": 0.002, "icir": 0.05}``）
            parent_trace_id: 父 trace_id

        Returns:
            本次事件的 trace_id
        """
        details: dict[str, Any] = {
            "model_id": model_id,
            "old_version": old_version,
            "new_version": new_version,
        }
        if metrics_delta:
            details["metrics_delta"] = metrics_delta
        return self._log_event(
            AuditEventType.MODEL_UPDATE,
            strategy_id=strategy_id,
            summary=f"模型更新: {model_id} {old_version} → {new_version}",
            details=details,
            parent_trace_id=parent_trace_id,
        )

    def log_code_modification(
        self,
        file_path: str,
        diff_summary: str,
        *,
        strategy_id: str | None = None,
        lines_changed: int = 0,
        parent_trace_id: str | None = None,
    ) -> str:
        """记录代码修改审计事件。

        Args:
            file_path: 被修改文件路径（相对于项目根目录）
            diff_summary: 修改摘要（一句话描述）
            strategy_id: 关联策略（可选）
            lines_changed: 变更行数
            parent_trace_id: 父 trace_id

        Returns:
            本次事件的 trace_id
        """
        return self._log_event(
            AuditEventType.CODE_MODIFICATION,
            strategy_id=strategy_id,
            summary=f"代码修改: {file_path} ({lines_changed} 行) — {diff_summary[:80]}",
            details={
                "file_path": file_path,
                "diff_summary": diff_summary,
                "lines_changed": lines_changed,
            },
            parent_trace_id=parent_trace_id,
        )

    # ── 查询接口 ──────────────────────────────────────────────────────

    def query_audit_trail(
        self,
        strategy_id: str | None = None,
        since: datetime | None = None,
        event_type: AuditEventType | None = None,
        limit: int = 500,
    ) -> list[AuditRecord]:
        """查询审计轨迹（从 JSONL 文件扫描满足条件的审计记录）。

        Returns:
            AuditRecord 列表，按时间戳升序排列
        """
        since_str = since.isoformat(timespec="seconds") if since else None
        evt_val = event_type.value if event_type else None
        records: list[AuditRecord] = []

        for fpath in sorted(self._log_dir.glob("agent_audit_*.jsonl")):
            try:
                for line in fpath.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if strategy_id and d.get("strategy_id") != strategy_id:
                        continue
                    if since_str and (d.get("timestamp") or "") < since_str:
                        continue
                    if evt_val and d.get("event_type") != evt_val:
                        continue
                    records.append(AuditRecord.from_dict(d))
                    if len(records) >= limit:
                        return records
            except OSError:
                continue

        return records

    def tail(self, n: int = 20) -> list[AuditRecord]:
        """返回最近 n 条审计记录（从最新日志文件向前扫描）。"""
        result: list[AuditRecord] = []
        for fpath in sorted(self._log_dir.glob("agent_audit_*.jsonl"), reverse=True):
            try:
                lines = fpath.read_text(encoding="utf-8").splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        result.append(AuditRecord.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, TypeError):
                        pass
                    if len(result) >= n:
                        break
            except OSError:
                continue
            if len(result) >= n:
                break
        return list(reversed(result[:n]))

    def get_model_version(self) -> str:
        """返回当前配置的 Agent 模型版本。"""
        return self._model_version


# ── 公开接口 ───────────────────────────────────────────────────────────

__all__ = [
    "AgentAudit",
    "AuditRecord",
    "AuditEventType",
]
