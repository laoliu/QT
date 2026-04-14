"""Agent 操作审计链路 — AI 代理操作可追溯记录"""
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

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    FACTOR_GENERATION = "factor_generation"
    BACKTEST_TRIGGER = "backtest_trigger"
    MODEL_UPDATE = "model_update"
    CODE_MODIFICATION = "code_modification"
    STRATEGY_DEPLOY = "strategy_deploy"
    COMPLIANCE_CHECK = "compliance_check"
    CUSTOM = "custom"


@dataclass
class AuditRecord:
    trace_id: str
    event_type: str
    timestamp: str
    strategy_id: Optional[str]
    model_version: Optional[str]
    triggered_by: str  # "agent" / "user" / "system"
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    parent_trace_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuditRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _make_trace_id() -> str:
    ts = datetime.now().strftime("%f")[-6:]
    uid = str(uuid.uuid4()).replace("-", "")[:8]
    return f"tr_{uid}_{ts}"


def _safe_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class AgentAudit:
    """Agent 操作审计链路

    记录因子生成、回测触发、模型更新、代码修改等操作,
    写入 JSONL 文件实现完整可追溯。
    """

    def __init__(self, log_dir: Path | str | None = None, model_version: str = "unknown"):
        self._log_dir = Path(log_dir) if log_dir else Path("logs/audit")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._model_version = model_version

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
        trace_id = _make_trace_id()
        rec = AuditRecord(
            trace_id=trace_id,
            event_type=event_type.value,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            strategy_id=strategy_id,
            model_version=self._model_version,
            triggered_by=triggered_by,
            summary=summary,
            details=details or {},
            parent_trace_id=parent_trace_id,
        )
        fname = f"agent_audit_{datetime.now():%Y%m%d}.jsonl"
        fpath = self._log_dir / fname
        try:
            with fpath.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("AgentAudit 写入失败: %s", exc)
        return trace_id

    def log_factor_generation(self, factor_name: str, prompt_hash: str, code: str,
                              *, strategy_id: str | None = None) -> str:
        code_hash = _safe_hash(code)
        return self._log_event(
            AuditEventType.FACTOR_GENERATION, strategy_id=strategy_id,
            summary=f"因子生成: {factor_name} (code_hash={code_hash})",
            details={"factor_name": factor_name, "prompt_hash": prompt_hash,
                     "code_hash": code_hash, "code_lines": len(code.splitlines())},
        )

    def log_backtest_trigger(self, strategy_id: str, config_hash: str,
                             triggered_by: str = "agent") -> str:
        return self._log_event(
            AuditEventType.BACKTEST_TRIGGER, strategy_id=strategy_id,
            summary=f"回测触发: {strategy_id} (config={config_hash[:8]})",
            details={"config_hash": config_hash}, triggered_by=triggered_by,
        )

    def log_model_update(self, model_id: str, old_version: str, new_version: str,
                         metrics_diff: dict | None = None) -> str:
        return self._log_event(
            AuditEventType.MODEL_UPDATE,
            summary=f"模型更新: {model_id} {old_version} → {new_version}",
            details={"model_id": model_id, "old_version": old_version,
                     "new_version": new_version, "metrics_diff": metrics_diff or {}},
        )

    def log_code_modification(self, file_path: str, diff_summary: str,
                              strategy_id: str | None = None) -> str:
        return self._log_event(
            AuditEventType.CODE_MODIFICATION, strategy_id=strategy_id,
            summary=f"代码修改: {file_path}",
            details={"file_path": file_path, "diff_summary": diff_summary},
        )

    def log_custom(self, event_name: str, details: dict | None = None,
                   strategy_id: str | None = None) -> str:
        return self._log_event(
            AuditEventType.CUSTOM, strategy_id=strategy_id,
            summary=event_name, details=details,
        )

    def query_audit_trail(
        self,
        strategy_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[AuditRecord]:
        """查询审计轨迹"""
        records = []
        for fpath in sorted(self._log_dir.glob("agent_audit_*.jsonl"), reverse=True):
            try:
                for line in fpath.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if strategy_id and d.get("strategy_id") != strategy_id:
                        continue
                    if event_type and d.get("event_type") != event_type:
                        continue
                    records.append(AuditRecord.from_dict(d))
                    if len(records) >= limit:
                        return records
            except Exception:
                continue
        return records
