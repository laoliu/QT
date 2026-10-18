"""Frozen cash/bonus-share events and raw-price portfolio accounting.

Events are accounting inputs, never model features. They are embedded as JSON
in the attested panel so a Parquet round trip preserves the complete ledger
input even when the symbol has no bar on its ex-date.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from collections import defaultdict
from datetime import date
import json
import hashlib
import math

import numpy as np
import pandas as pd


ACTION_COLUMN = "corporate_actions_json"


@dataclass(frozen=True)
class CorporateAction:
    action_id: str
    symbol: str
    announce_date: date
    record_date: date
    ex_date: date
    cash_per_share: float = 0.0  # Gross dividend; holding-period tax is separate.
    bonus_per_share: float = 0.0
    pay_date: date | None = None
    share_list_date: date | None = None
    # Differential distributions use a diluted exchange reference amount,
    # while eligible holders receive the actual cash_per_share.
    reference_cash_per_share: float | None = None
    reference_bonus_per_share: float | None = None

    def __post_init__(self) -> None:
        for name in ("announce_date", "record_date", "ex_date", "pay_date", "share_list_date"):
            value = getattr(self, name)
            if value is None and name in {"announce_date", "record_date", "ex_date"}:
                raise ValueError(f"corporate action requires a valid {name}")
            if value is not None:
                parsed = pd.Timestamp(value)
                if pd.isna(parsed):
                    raise ValueError(f"corporate action requires a valid {name}")
                object.__setattr__(self, name, parsed.date())
        if not self.action_id or not self.symbol:
            raise ValueError("corporate action requires action_id and symbol")
        if not self.announce_date <= self.record_date < self.ex_date:
            raise ValueError("corporate action dates require announce <= record < ex")
        for name in ("cash_per_share", "bonus_per_share"):
            object.__setattr__(self, name, float(getattr(self, name)))
        if not all(math.isfinite(v) and v >= 0 for v in (self.cash_per_share, self.bonus_per_share)):
            raise ValueError("corporate action amounts must be finite and non-negative")
        if self.reference_cash_per_share is not None:
            reference = float(self.reference_cash_per_share)
            if not math.isfinite(reference) or not 0 <= reference <= self.cash_per_share:
                raise ValueError("reference cash must be finite and within the actual cash distribution")
            object.__setattr__(self, "reference_cash_per_share", reference)
        if self.reference_bonus_per_share is not None:
            reference = float(self.reference_bonus_per_share)
            if not math.isfinite(reference) or not 0 <= reference <= self.bonus_per_share:
                raise ValueError("reference bonus must be finite and within the actual share distribution")
            object.__setattr__(self, "reference_bonus_per_share", reference)
        if self.cash_per_share == self.bonus_per_share == 0:
            raise ValueError("corporate action must distribute cash or shares")
        for amount, when in ((self.cash_per_share, self.pay_date), (self.bonus_per_share, self.share_list_date)):
            if amount and (when is None or when < self.ex_date):
                raise ValueError("corporate action requires settlement dates on/after ex-date")

    def to_wire(self) -> dict:
        return {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(self).items()
                if k not in {"reference_cash_per_share", "reference_bonus_per_share"} or v is not None}

    @property
    def reference_cash(self) -> float:
        return self.cash_per_share if self.reference_cash_per_share is None else self.reference_cash_per_share

    @property
    def reference_bonus(self) -> float:
        return self.bonus_per_share if self.reference_bonus_per_share is None else self.reference_bonus_per_share


def apply_action_unit_corrections(
    actions: list[CorporateAction], corrections: list[dict], evidence: dict[str, bytes], *, as_of,
) -> tuple[list[CorporateAction], list[dict]]:
    """Apply reviewed, event-specific issuer terms, never infer from prices.

    Full before/after terms and a dated issuer excerpt identify every change.
    Persist the returned audit in signed snapshot metadata. Evidence confirms
    the reviewed source, not an automatic interpretation of its legal wording.
    """
    result = {a.action_id: a for a in actions}
    if len(result) != len(actions):
        raise ValueError("duplicate corporate action_id")
    seen = set()
    audit = []
    for record in corrections:
        before, after = CorporateAction(**record["before"]), CorporateAction(**record["after"])
        if before.action_id in seen or result.get(before.action_id) != before:
            raise ValueError("unit correction must match exactly one unmodified source event")
        seen.add(before.action_id)
        immutable = ("action_id", "symbol", "record_date", "ex_date", "pay_date", "share_list_date")
        if any(getattr(before, k) != getattr(after, k) for k in immutable):
            raise ValueError("unit correction cannot change event identity or settlement dates")
        notice_ts = pd.Timestamp(record["notice_date"])
        if pd.isna(notice_ts):
            raise ValueError("unit correction requires a valid notice date")
        notice = notice_ts.date()
        if notice > pd.Timestamp(as_of).date() or notice > before.record_date:
            raise ValueError("unit correction notice must be available by record date and snapshot as_of")
        if after.announce_date != max(before.announce_date, notice):
            raise ValueError("unit correction must retain the latest applicable announcement date")
        for name in ("source_unit", "trading_unit", "issuer", "source_url", "evidence_excerpt"):
            if not str(record.get(name, "")).strip():
                raise ValueError(f"unit correction requires {name}")
        if not record["source_url"].startswith("https://"):
            raise ValueError("unit correction requires an HTTPS issuer announcement source")
        digest = record["evidence_sha256"]
        content = evidence.get(digest)
        if content is None or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("unit correction evidence hash mismatch or missing evidence")
        if record["evidence_excerpt"] not in content.decode("utf-8"):
            raise ValueError("unit correction excerpt absent from frozen evidence")
        result[before.action_id] = after
        audit.append({**record, "before": before.to_wire(), "after": after.to_wire(),
                      "notice_date": notice.isoformat(), "method": "reviewed_issuer_event_terms"})
    return sorted(result.values(), key=lambda a: (a.ex_date, a.action_id)), audit


def attach_corporate_actions(panel: pd.DataFrame, actions: list[CorporateAction]) -> pd.DataFrame:
    """Attach each symbol's frozen events once, covered by the panel hash."""
    out = panel.copy()
    out[ACTION_COLUMN] = "[]"
    seen: set[str] = set()
    effective: set[tuple[str, date]] = set()
    grouped: dict[str, list[CorporateAction]] = defaultdict(list)
    for action in actions:
        if action.action_id in seen or (action.symbol, action.ex_date) in effective:
            raise ValueError("duplicate corporate action_id or symbol/ex_date; consolidate event terms explicitly")
        seen.add(action.action_id)
        effective.add((action.symbol, action.ex_date))
        grouped[action.symbol].append(action)
    first_rows = out.sort_values("trade_date").groupby("symbol", sort=False).head(1)
    first_indices = dict(zip(first_rows.symbol, first_rows.index))
    for symbol, events in grouped.items():
        if symbol not in first_indices:
            raise ValueError(f"corporate action symbol absent from panel: {symbol}")
        payload = [a.to_wire() for a in sorted(events, key=lambda a: (a.ex_date, a.action_id))]
        out.at[first_indices[symbol], ACTION_COLUMN] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return out


def read_corporate_actions(panel: pd.DataFrame) -> list[CorporateAction]:
    actions: list[CorporateAction] = []
    if ACTION_COLUMN not in panel:
        return actions
    if panel[ACTION_COLUMN].isna().any():
        raise ValueError("corporate_actions_json cannot contain missing values")
    seen: set[str] = set()
    effective: set[tuple[str, date]] = set()
    populated = panel.loc[panel[ACTION_COLUMN].ne("[]"), ["symbol", ACTION_COLUMN]]
    for symbol, encoded in populated.itertuples(index=False, name=None):
        payload = json.loads(encoded)
        if not isinstance(payload, list):
            raise ValueError("corporate_actions_json must encode a list")
        for record in payload:
            action = CorporateAction(**record)
            if action.symbol != symbol or action.action_id in seen or (action.symbol, action.ex_date) in effective:
                raise ValueError("corporate action symbol mismatch or duplicate action_id")
            seen.add(action.action_id)
            effective.add((action.symbol, action.ex_date))
            actions.append(action)
    return sorted(actions, key=lambda a: (a.ex_date, a.action_id))


def normalize_tushare_dividends(
    history: pd.DataFrame, *, start_date=None, end_date=None,
) -> list[CorporateAction]:
    """Normalize implemented terms within an initially cash-only run's scope.

    TuShare represents an absent distribution leg as null. It is absent only
    when the other leg is positive and this leg has no settlement date;
    a missing amount with a scheduled settlement remains an error.
    """
    if history.empty:
        return []
    required = {"ts_code", "end_date", "ann_date", "div_proc", "record_date", "ex_date", "cash_div_tax", "stk_div"}
    if required - set(history):
        raise ValueError(f"TuShare dividend history missing fields: {sorted(required - set(history))}")
    actions = {}
    for row in history.to_dict("records"):
        if row["div_proc"] != "实施":
            continue
        def optional_date(name):
            value = row.get(name)
            return None if value is None or pd.isna(value) or value == "" else pd.Timestamp(value).date()
        announced = optional_date("imp_ann_date") or optional_date("ann_date")
        ex_date = optional_date("ex_date")
        known_dates = [value for value in (
            announced, optional_date("record_date"), ex_date,
            optional_date("pay_date"), optional_date("div_listdate"),
        ) if value is not None]
        if ((start_date is not None and (
                (ex_date is not None and ex_date < pd.Timestamp(start_date).date())
                or (known_dates and max(known_dates) < pd.Timestamp(start_date).date())))
                or (end_date is not None and announced is not None and announced > pd.Timestamp(end_date).date())):
            continue
        cash, bonus = row["cash_div_tax"], row["stk_div"]
        if pd.isna(cash) and pd.notna(bonus) and float(bonus) > 0 and optional_date("pay_date") is None:
            cash = 0.
        if pd.isna(bonus) and pd.notna(cash) and float(cash) > 0 and optional_date("div_listdate") is None:
            bonus = 0.
        if pd.isna(cash) or pd.isna(bonus):
            raise ValueError(f"unknown TuShare dividend amount for {row['ts_code']} on {row['ex_date']}")
        cash, bonus = float(cash), float(bonus)
        if cash == bonus == 0:
            continue
        action = CorporateAction(
            action_id=f"tushare:{row['ts_code']}:{row['end_date']}:{row['ex_date']}",
            symbol=str(row["ts_code"]), announce_date=announced,
            record_date=optional_date("record_date"), ex_date=ex_date,
            cash_per_share=cash, bonus_per_share=bonus,
            pay_date=optional_date("pay_date"), share_list_date=optional_date("div_listdate"),
        )
        key = (action.symbol, str(row["end_date"]), action.ex_date)
        if key in actions and actions[key] != action:
            raise ValueError(f"conflicting TuShare dividend terms for {action.symbol} on {action.ex_date}")
        actions[key] = action
    by_effective = defaultdict(list)
    for action in actions.values():
        by_effective[(action.symbol, action.ex_date)].append(action)
    combined = []
    for (symbol, ex_date), group in sorted(by_effective.items()):
        if len(group) == 1:
            combined.append(group[0])
            continue
        # Distinct report periods may distribute on the same effective date.
        # Sum independent legs only when their entitlement/settlement agrees;
        # duplicates within a report period were checked above, not summed.
        records = {action.record_date for action in group}
        payments = {action.pay_date for action in group if action.cash_per_share > 0}
        listings = {action.share_list_date for action in group if action.bonus_per_share > 0}
        if len(records) != 1 or len(payments) > 1 or len(listings) > 1:
            raise ValueError(f"incompatible TuShare dividend settlement for {symbol} on {ex_date}")
        combined.append(replace(
            group[0], action_id="tushare:combined:" + "|".join(sorted(a.action_id for a in group)),
            announce_date=max(a.announce_date for a in group),
            cash_per_share=sum(a.cash_per_share for a in group),
            bonus_per_share=sum(a.bonus_per_share for a in group),
            pay_date=next(iter(payments), None), share_list_date=next(iter(listings), None),
        ))
    return combined


def raw_accounting_bars(panel: pd.DataFrame) -> pd.DataFrame:
    """Use raw currency prices for lots/fees/valuation, retaining feature data."""
    out = panel.copy()
    adjusted = out.get("price_adjustment", pd.Series("raw", index=out.index)).eq("point_in_time_factor")
    for column in ("open", "close"):
        raw = f"{column}_raw"
        values = pd.to_numeric(out[raw] if raw in out else out[column], errors="coerce")
        if raw not in out and adjusted.any():
            factors = pd.to_numeric(out.get("adj_factor", pd.Series(np.nan, index=out.index)), errors="coerce")
            if not (np.isfinite(factors[adjusted]) & factors[adjusted].gt(0)).all():
                raise ValueError("raw accounting requires positive finite adj_factor or raw prices")
            values = values.where(~adjusted, values / factors)
        if not (np.isfinite(values) & values.gt(0)).all():
            raise ValueError("raw accounting requires positive finite opening/closing prices")
        out[column] = values
    return out


class CorporateActionLedger:
    """Record-date entitlement, ex-date receivables, dated cash/share delivery."""

    def __init__(self, panel: pd.DataFrame):
        self.actions = read_corporate_actions(panel)
        self.by_symbol: dict[str, list[CorporateAction]] = defaultdict(list)
        self.by_record_date: dict[date, list[CorporateAction]] = defaultdict(list)
        for action in self.actions:
            self.by_symbol[action.symbol].append(action)
            self.by_record_date[action.record_date].append(action)
        self.next_action = 0
        self.entitlements: dict[str, int] = {}
        self.pending: dict[str, tuple[CorporateAction, float, int]] = {}
        self.log: list[dict] = []
        self.last_factor: dict[str, tuple[date, float, float, float]] = {}
        quantum = pd.to_numeric(panel.get("adj_factor_rounding_unit", pd.Series(0., index=panel.index)), errors="coerce")
        if not (np.isfinite(quantum) & quantum.ge(0) & quantum.le(.001)).all():
            raise ValueError("adj_factor_rounding_unit must be finite and between zero and 0.001")
        self.cash_distributed = 0.0
        self.shares_distributed = 0
        self.start_date = pd.Timestamp(panel.trade_date.min()).date() if not panel.empty else date.max

    def before_open(self, day: date, holdings: dict[str, int], last_prices: dict[str, float], buy_dates: dict) -> float:
        while self.next_action < len(self.actions) and self.actions[self.next_action].ex_date <= day:
            action = self.actions[self.next_action]
            self.next_action += 1
            if action.record_date >= self.start_date and action.action_id not in self.entitlements:
                raise ValueError("corporate action record date is missing from the replay calendar")
            qty = self.entitlements.get(action.action_id, 0)
            cash = qty * action.cash_per_share
            shares = qty * action.bonus_per_share
            rounded = round(shares)
            if not math.isclose(shares, rounded, rel_tol=0, abs_tol=1e-8):
                raise ValueError("fractional bonus shares require an explicit allocation/cash-in-lieu record")
            if action.symbol in last_prices:
                price = (last_prices[action.symbol] - action.reference_cash) / (1 + action.reference_bonus)
                if price <= 0:
                    raise ValueError("corporate action implies a non-positive ex-price")
                last_prices[action.symbol] = price
            if cash or rounded:
                self.pending[action.action_id] = (action, cash, rounded)
                self.log.append({"action_id": action.action_id, "symbol": action.symbol,
                                 "date": day.isoformat(), "event": "ex_entitlement",
                                 "eligible_shares": qty, "cash_receivable": cash, "share_receivable": rounded})
        credit = 0.0
        for key, (action, cash, shares) in list(self.pending.items()):
            if cash and action.pay_date <= day:
                credit += cash
                self.cash_distributed += cash
                self.log.append({"action_id": key, "symbol": action.symbol, "date": day.isoformat(),
                                 "event": "cash_paid", "amount": cash})
                cash = 0.0
            if shares and action.share_list_date <= day:
                holdings[action.symbol] = holdings.get(action.symbol, 0) + shares
                buy_dates.setdefault(action.symbol, pd.Timestamp(action.record_date))
                self.shares_distributed += shares
                self.log.append({"action_id": key, "symbol": action.symbol, "date": day.isoformat(),
                                 "event": "shares_delivered", "qty": shares})
                shares = 0
            if cash or shares:
                self.pending[key] = (action, cash, shares)
            else:
                del self.pending[key]
        return credit

    def check_factor_changes(self, day: date, frame: pd.DataFrame, holdings: dict[str, int]) -> None:
        for symbol, row in frame.iterrows():
            factor = float(row.get("adj_factor", 1.0))
            quantum = float(row.get("adj_factor_rounding_unit", 0.0))
            if not math.isfinite(factor) or factor <= quantum / 2:
                raise ValueError("accounting requires positive finite adj_factor")
            prior = self.last_factor.get(symbol)
            suspended = bool(row.get("is_suspended", False))
            reference_close = prior[2] if prior and suspended else float(row["close"])
            owned = holdings.get(symbol, 0) or any(a.symbol == symbol and shares for a, _, shares in self.pending.values())
            if prior and owned:
                events = [a for a in self.by_symbol.get(symbol, []) if prior[0] < a.ex_date <= day]
                changed = abs(factor - prior[1]) > max(1e-10 * prior[1], (quantum + prior[3]) / 2)
                if changed and not events:
                    raise ValueError(f"unexplained adjustment factor change for held {symbol}; corporate action history required")
                if events:
                    expected = prior[2]
                    for action in events:
                        expected = (expected - action.reference_cash) / (1 + action.reference_bonus)
                    # A declared vendor quantization has a bounded ratio
                    # error. It never changes cash entitlement or factor
                    # values, and defaults to zero for undeclared sources.
                    lower = prior[2] * (prior[1] - prior[3] / 2) / (factor + quantum / 2)
                    upper = prior[2] * (prior[1] + prior[3] / 2) / (factor - quantum / 2)
                    # Allow one currency tick of ex-price rounding. Material
                    # unexplained splits/rights issues still fail closed.
                    if not lower - .011 <= expected <= upper + .011:
                        raise ValueError(f"corporate action does not reconcile adj_factor for held {symbol}")
                    if "pre_close" in row and not suspended and len(events) == 1 and events[0].ex_date == day:
                        reference = float(row["pre_close"])
                        if not math.isfinite(reference) or not math.isclose(expected, reference, rel_tol=1e-6, abs_tol=.011):
                            raise ValueError(f"corporate action does not reconcile exchange pre_close for held {symbol}")
                    if suspended:
                        reference_close = expected
            self.last_factor[symbol] = (day, factor, reference_close, quantum)

    def after_close(self, day: date, holdings: dict[str, int]) -> None:
        for action in self.by_record_date.get(day, []):
            if any(a.symbol == action.symbol and shares for a, _, shares in self.pending.values()):
                raise ValueError("overlapping bonus-share registration requires explicit entitlement records")
            self.entitlements[action.action_id] = holdings.get(action.symbol, 0)

    def receivables(self, prices: dict[str, float]) -> tuple[float, float]:
        cash = sum(cash for _, cash, _ in self.pending.values())
        stock = sum(shares * prices[action.symbol] for action, _, shares in self.pending.values() if shares)
        return cash, stock
