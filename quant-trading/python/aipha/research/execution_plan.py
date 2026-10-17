"""Versioned research-to-execution plan for Paper and QMT consumers."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .contracts import TargetPosition


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """A broker-neutral target plan with explicit provenance."""

    schema_version: int
    mode: str
    run_id: str
    model_id: str
    signal_id: str
    rebalance_date: date
    targets: tuple[TargetPosition, ...]
    plan_id: str

    def validate(self, *, allow_live: bool = False) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported execution plan schema_version")
        if self.mode not in {"paper", "live"}:
            raise ValueError("execution plan mode must be paper or live")
        if self.mode == "live" and not allow_live:
            raise ValueError("live execution requires an explicit allow_live gate")
        if not self.run_id or not self.model_id or not self.signal_id:
            raise ValueError("execution plan requires run_id, model_id and signal_id")
        if not self.targets:
            raise ValueError("execution plan contains no targets")
        if abs(sum(float(target.target_weight) for target in self.targets)) > 1.0 + 1e-9:
            raise ValueError("execution plan target weights exceed 100%")
        for target in self.targets:
            if not target.run_id or not target.model_id or not target.signal_id:
                raise ValueError("every target must carry run_id, model_id and signal_id")
            if target.run_id != self.run_id or target.model_id != self.model_id:
                raise ValueError("target provenance does not match execution plan")
            if target.rebalance_date != self.rebalance_date:
                raise ValueError("target rebalance date does not match execution plan")
            if not 0 <= float(target.target_weight) <= 1:
                raise ValueError("target weights must be between zero and one")
        signal_ids = sorted(target.signal_id for target in self.targets)
        batch_payload = "|".join([self.run_id, self.model_id, *signal_ids, self.rebalance_date.isoformat()])
        expected_signal = "signals-" + hashlib.sha256(batch_payload.encode("utf-8")).hexdigest()[:20]
        if self.signal_id != expected_signal:
            raise ValueError("execution plan signal_id does not match target provenance")
        plan_payload = "|".join([self.run_id, self.model_id, self.signal_id, self.rebalance_date.isoformat()])
        expected_plan_id = "plan-" + hashlib.sha256(plan_payload.encode("utf-8")).hexdigest()[:20]
        if self.plan_id != expected_plan_id:
            raise ValueError("execution plan plan_id does not match its contents")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "run_id": self.run_id,
            "model_id": self.model_id,
            "signal_id": self.signal_id,
            "rebalance_date": self.rebalance_date.isoformat(),
            "plan_id": self.plan_id,
            "targets": [asdict(target) for target in self.targets],
        }

    def write(self, path: str | Path, *, allow_live: bool = False) -> Path:
        self.validate(allow_live=allow_live)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return target


def build_execution_plan(
    targets: Iterable[TargetPosition],
    *,
    mode: str = "paper",
    run_id: str | None = None,
    model_id: str | None = None,
    allow_live: bool = False,
) -> ExecutionPlan:
    rows = tuple(targets)
    if not rows:
        raise ValueError("cannot build an empty execution plan")
    resolved_run = run_id or rows[0].run_id
    resolved_model = model_id or rows[0].model_id
    if not resolved_run or not resolved_model:
        raise ValueError("targets must contain run_id and model_id")
    signal_ids = sorted({row.signal_id for row in rows if row.signal_id})
    if len(signal_ids) != len(rows):
        raise ValueError("every target must have a unique signal_id")
    rebalance_dates = {row.rebalance_date for row in rows}
    if len(rebalance_dates) != 1:
        raise ValueError("execution plan can contain one rebalance date only")
    batch_payload = "|".join([resolved_run, resolved_model, *signal_ids, rows[0].rebalance_date.isoformat()])
    batch_signal_id = "signals-" + hashlib.sha256(batch_payload.encode("utf-8")).hexdigest()[:20]
    payload = "|".join([resolved_run, resolved_model, batch_signal_id, rows[0].rebalance_date.isoformat()])
    plan_id = "plan-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    plan = ExecutionPlan(1, mode, resolved_run, resolved_model, batch_signal_id, rows[0].rebalance_date, rows, plan_id)
    plan.validate(allow_live=allow_live)
    return plan


def read_execution_plan(path: str | Path, *, allow_live: bool = False) -> ExecutionPlan:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    targets = tuple(
        TargetPosition(
            rebalance_date=date.fromisoformat(str(item["rebalance_date"])[:10]),
            symbol=str(item["symbol"]),
            target_weight=float(item["target_weight"]),
            reason=str(item["reason"]),
            risk_flags=tuple(item.get("risk_flags", ())),
            run_id=str(item.get("run_id", "")),
            model_id=str(item.get("model_id", "")),
            signal_id=str(item.get("signal_id", "")),
        )
        for item in raw.get("targets", [])
    )
    plan = ExecutionPlan(
        schema_version=int(raw.get("schema_version", 0)),
        mode=str(raw.get("mode", "")),
        run_id=str(raw.get("run_id", "")),
        model_id=str(raw.get("model_id", "")),
        signal_id=str(raw.get("signal_id", "")),
        rebalance_date=date.fromisoformat(str(raw.get("rebalance_date", ""))[:10]),
        targets=targets,
        plan_id=str(raw.get("plan_id", "")),
    )
    plan.validate(allow_live=allow_live)
    return plan


def execute_target_plan(
    gateway: Any,
    plan: ExecutionPlan,
    *,
    prices: dict[str, float],
    account_value: float,
    current_positions: dict[str, int] | None = None,
    allow_live: bool = False,
) -> list[str]:
    """Translate a validated target plan into broker orders.

    The gateway receives only orders carrying the plan provenance.  This
    helper deliberately requires ``allow_live=True`` for a live plan and is
    the single adapter used by the QMT plan-consumption method.
    """
    plan.validate(allow_live=allow_live)
    if account_value <= 0:
        raise ValueError("account_value must be positive")
    positions = dict(current_positions or {})
    desired: dict[str, int] = {}
    for target in plan.targets:
        price = float(prices.get(target.symbol, 0.0))
        if price <= 0:
            raise ValueError(f"missing positive price for {target.symbol}")
        desired[target.symbol] = int(float(target.target_weight) * account_value / price / 100) * 100
    symbols = sorted(set(positions) | set(desired))
    order_ids: list[str] = []
    from aipha.execution.gateway import GatewayOrder

    deltas = {symbol: desired.get(symbol, 0) - positions.get(symbol, 0) for symbol in symbols}
    # Sell first so released cash can finance buys under A-share settlement.
    for side in ("SELL", "BUY"):
        for symbol in symbols:
            delta = deltas[symbol]
            if delta == 0 or (side == "BUY" and delta < 0) or (side == "SELL" and delta > 0):
                continue
            qty = abs(delta)
            target = next((item for item in plan.targets if item.symbol == symbol), None)
            signal_id = target.signal_id if target is not None else plan.signal_id
            order = GatewayOrder(
                order_id=f"{plan.plan_id}:{symbol}:{side}",
                symbol=symbol,
                side=side,
                qty=qty,
                order_type="MARKET",
                metadata={
                    "execution_plan_id": plan.plan_id,
                    "run_id": plan.run_id,
                    "model_id": plan.model_id,
                    "signal_id": signal_id,
                    "mode": plan.mode,
                },
            )
            order_ids.append(str(gateway.submit_order(order)))
    return order_ids
