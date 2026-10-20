"""Bounded, read-only projections for the research operations UI.

Never import a broker, unpickle a model, prepare data, or create a ledger.
The deployment file is server-selected; HTTP callers cannot supply paths.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re

SHANGHAI = timezone(timedelta(hours=8))
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def read_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("artifact exceeds display size limit")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def pick(value, keys):
    return {key: value[key] for key in keys.split() if key in value} if isinstance(value, dict) else {}


def target_reason_label(reason):
    """Translate persisted target reason codes into concise UI text."""
    value = str(reason or "").strip()
    match = re.fullmatch(r"ml_rank_(\d+)", value, re.IGNORECASE)
    if match:
        return f"模型评分排名第 {int(match.group(1))} 名"
    return value or "未提供原因"


def clean(value, secrets=()):
    if isinstance(value, dict):
        return {str(k): clean(v, secrets) for k, v in value.items()
                if not re.search(r"token|secret|password|signature|account_id|accountid|env_file", str(k), re.I)}
    if isinstance(value, (list, tuple)):
        return [clean(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if len(secret) >= 8:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)((?:token|password|secret|account_id|accountid)\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", value)
        return value[:8000]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return clean(value.item(), secrets)
    return value


def tail(path, limit=100, max_bytes=48_000):
    path = Path(path)
    if not path.is_file():
        return []
    with path.open("rb") as handle:
        size = path.stat().st_size
        offset = max(0, size - max_bytes)
        # UTF-16 redirect logs need an even byte offset. Drop the first partial
        # line after decoding, not with binary readline (which would split CR/LF).
        offset -= offset % 2
        handle.seek(offset)
        raw = handle.read(max_bytes)
    # PowerShell 5.1 redirects native output to UTF-16; other writers use UTF-8.
    encoding = "utf-16-le" if b"\x00" in raw[:200] or raw.startswith(b"\xff\xfe") else "utf-8-sig"
    text = raw.decode(encoding, errors="replace").lstrip("\ufeff")
    if offset:
        text = text.split("\n", 1)[-1]
    return text.splitlines()[-limit:]


def target_rows(rows, names=None):
    aliases = {"rebalance_date": "rebalanceDate", "target_weight": "targetWeight", "risk_flags": "riskFlags",
               "run_id": "runId", "model_id": "modelId", "signal_id": "signalId"}
    result = [pick({aliases.get(k, k): v for k, v in row.items()},
                 "symbol rebalanceDate targetWeight reason reasonLabel riskFlags runId modelId signalId") for row in rows[:500]]
    for row in result:
        reason = row.get("reasonLabel") or row.get("reason")
        if reason is not None:
            row["reasonLabel"] = target_reason_label(reason)
    if names is not None:
        for row in result:
            row["name"] = names.get(str(row.get("symbol", "")).upper())
    return result


def receipt_row(row):
    result = pick(row, "trade_date committed_at mode nav cash accepted_plan_id replay_hash data_snapshot_id model_config_hash")
    result["positions"] = row.get("positions", {})
    result["trades"] = [pick(t, "trade_date signal_date symbol side qty price notional commission transfer_fee stamp_tax slippage_cost signal_id run_id model_id")
                        for t in row.get("new_trades", [])[:500]]
    result["metrics"] = row.get("metrics", {})
    return result


class OperationsView:
    def __init__(self, deployment_path, *, now=None):
        import yaml
        self.now = now or datetime.now(timezone.utc)
        self.now = self.now.replace(tzinfo=SHANGHAI) if self.now.tzinfo is None else self.now
        self.path = Path(deployment_path).resolve()
        self.deployment = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        if not isinstance(self.deployment, dict) or self.deployment.get("mode") != "paper":
            raise ValueError("only Paper deployments can be observed")
        def resolve(key):
            path = Path(self.deployment[key]).expanduser()
            return (path if path.is_absolute() else self.path.parent / path).resolve()
        self.root, self.model = resolve("output_root"), resolve("model_path")
        self.run = self.model.parent
        self.secrets = []
        if self.deployment.get("env_file"):
            from dotenv import dotenv_values, load_dotenv
            env = resolve("env_file")
            self.secrets = [v for v in dotenv_values(env).values() if v]
            load_dotenv(env, override=False)
        self.secrets += [v for k, v in os.environ.items() if re.search(r"TOKEN|SECRET|PASSWORD|SNAPSHOT_KEY", k)]
        self.issues = []
        self._stock_names = None

    def named_targets(self, rows):
        """Current local names are display metadata only; never rewrite plans."""
        return target_rows(rows, self.stock_name_map())

    def stock_name_map(self):
        if self._stock_names is None:
            self._stock_names = self.section("stock_names", self.stock_names, {})
        return self._stock_names

    def stock_names(self):
        import pandas as pd
        cache = self.deployment.get("cache_dir")
        if not cache:
            return {}
        root = Path(cache).expanduser()
        root = root if root.is_absolute() else self.path.parent / root
        path = root / "stock_basic.parquet"
        if not path.is_file():
            return {}
        frame = pd.read_parquet(path, columns=["ts_code", "name"]).dropna(subset=["ts_code", "name"])
        names = {}
        for symbol, name in frame.itertuples(index=False, name=None):
            symbol, name = str(symbol).strip().upper(), str(name).strip()
            if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol) and name:
                names[symbol] = name
        return names

    def section(self, name, fn, default):
        try:
            return fn()
        except Exception as exc:
            # Raw errors can contain paths/URLs/credentials. Expose only a type.
            self.issues.append({"section": name, "error_type": type(exc).__name__, "message": "读取或校验失败，请检查本机证据"})
            return default

    def dates(self):
        directory = self.root / "sessions"
        return sorted((p.name for p in directory.iterdir() if p.is_dir() and DATE.fullmatch(p.name)), reverse=True) if directory.is_dir() else []

    def predictions(self, scope="paper", day=None, offset=0, limit=50):
        import pandas as pd
        if scope not in {"paper", "research"} or (day and not DATE.fullmatch(day)):
            raise ValueError("invalid prediction scope/date")
        if day:
            datetime.strptime(day, "%Y-%m-%d")
        if not 0 <= offset <= 1_000_000 or not 1 <= limit <= 100:
            raise ValueError("invalid pagination")
        if scope == "paper":
            dates = [d for d in self.dates() if (self.root / "sessions" / d / "predictions.parquet").is_file()]
            day = day or (dates[0] if dates else None)
            path = self.root / "sessions" / day / "predictions.parquet" if day else None
        else:
            path = self.run / "oos_predictions.parquet"
            dates = []
        if path is None or not path.is_file():
            return {"scope": scope, "date": day, "dates": dates, "total": 0, "rows": [], "offset": offset, "limit": limit}
        frame = pd.read_parquet(path, columns=["trade_date", "symbol", "score", "rank", "model_id", "feature_version"])
        frame["trade_date"] = pd.to_datetime(frame.trade_date).dt.strftime("%Y-%m-%d")
        if scope == "research":
            dates = sorted(frame.trade_date.unique().tolist(), reverse=True)
        day = day or (dates[0] if dates else None)
        frame = frame.loc[frame.trade_date.eq(day)].sort_values(["rank", "symbol"], kind="stable")
        rows = frame.iloc[offset:offset + limit].to_dict("records")
        names = self.stock_name_map()
        for row in rows:
            row["name"] = names.get(str(row["symbol"]).strip().upper())
        return clean({"scope": scope, "date": day, "dates": dates, "total": len(frame), "offset": offset, "limit": limit,
                      "rows": rows}, self.secrets)

    def research(self):
        import pandas as pd
        manifest = read_json(self.run / "run_manifest.json", {})
        report = read_json(self.run / "report.json", {})
        result = pick(manifest, "run_id data_provider model_type data_snapshot_id feature_version model_config_hash train_start train_end oos_start oos_end metrics")
        result.update(pick(report, "data_as_of n_oos_predictions n_target_positions n_trades n_unexecutable feature_missing_rate feature_missing_rates"))
        result["folds"] = [pick(f, "fold train_start train_end test_start test_end train_samples test_samples mean_ic ic_ir is_mean_ic purge_start purge_end purge_sessions")
                           for f in report.get("walk_forward", {}).get("folds", [])]
        result["diagnostics"] = pick(report.get("diagnostics", {}), "is_oos_decay benchmark long_short exposures style_exposures")
        returns = self.run / "portfolio_returns.parquet"
        result["nav_history"] = pd.read_parquet(returns).tail(5000).to_dict("records") if returns.is_file() else []
        targets = read_json(self.run / "target_positions.json", [])
        dates = sorted({r.get("rebalanceDate", "") for r in targets})
        result["target_date"] = dates[-1] if dates else None
        result["targets"] = self.named_targets([r for r in targets if r.get("rebalanceDate") == result["target_date"]])
        return result

    def ledger(self):
        from .paper_monitor import inspect_paper_book
        from .paper_book import read_paper_book_evidence
        book = self.root / "paper.sqlite3"
        required = self.deployment.get("required_operating_days", 5)
        if not book.is_file():
            missed = str(self.now.astimezone(SHANGHAI).date()) > str(self.deployment["start_date"])
            return {"status": "missing_first_session" if missed else "awaiting_first_receipt", "passed": False,
                    "consecutive_valid_days": 0, "required_days": required, "nav_history": [], "receipts": [],
                    "message": "首日已过且无正式账本，需明确新起点" if missed else "等待首个盘后正式回执；预演不计天数"}
        monitor = inspect_paper_book(book, now=self.now, required_days=required)
        result = pick(monitor, "status passed consecutive_valid_days total_valid_days longest_streak required_days first_valid_date last_valid_date invalid_days gaps age_hours stale message nav_history latest_trade_date committed_at")
        # A corrupt/unverified ledger must never yield trusted receipts or holdings.
        if monitor["status"] == "invalid":
            result["receipts"] = []
            return result
        evidence = read_paper_book_evidence(book)
        result["receipts"] = [receipt_row(row) for row in evidence["receipts"][-60:]][::-1]
        result["metrics"] = evidence["replay"].get("metrics", {})
        return result

    def overview(self):
        dep = self.deployment
        model_manifest = self.section("model", lambda: read_json(self.model / "model_manifest.json", {}), {})
        digest = self.section("model_hash", lambda: hashlib.sha256((self.model / "model_manifest.json").read_bytes()).hexdigest(), None)
        progress = self.section("progress", lambda: read_json(self.root / "progress.json", {}), {})
        latest = self.section("latest", lambda: read_json(self.root / "latest.json", {}), {})
        process = self.section("process", lambda: read_json(self.root / "scheduler/latest_process_result.json", {}), {})
        failure = self.section("failure", lambda: read_json(self.root / "latest_failure.json", {}), {})
        events = self.section("events", lambda: [json.loads(line) for line in tail(self.root / "phase_events.jsonl", 100) if line.strip()], [])
        log_path = Path(process.get("log_path", ""))
        log_root = (self.root / "scheduler").resolve()
        logs = []
        if log_path.is_file() and log_path.resolve().is_relative_to(log_root):
            logs = self.section("logs", lambda: tail(log_path), [])
        research = self.section("research", self.research, {})
        ledger = self.section("ledger", self.ledger, {"status": "unavailable", "passed": False})
        sessions = []
        for day in self.dates()[:60]:
            directory = self.root / "sessions" / day
            op = self.section("session:" + day, lambda d=directory: read_json(d / "operation.json", {}), {})
            meta = self.section("metadata:" + day, lambda d=directory: read_json(d / "snapshot.metadata.json", {}), {})
            plan = self.section("plan:" + day, lambda d=directory: read_json(d / "plan.json", {}), {})
            sessions.append({"date": day, **pick(op, "status operational run_id model_id predictions targets"),
                             "snapshot_end": meta.get("end_date"), "targets_detail": self.named_targets(plan.get("targets", []))})
        # data_as_of is the OOS evaluation end, not necessarily raw data coverage.
        attestation = self.section("snapshot_metadata", lambda: read_json(self.run / "input_snapshot.metadata.json", {}), {})
        alerts = []
        if digest is None or digest != dep.get("model_manifest_sha256"):
            alerts.append({"level": "error", "message": "部署模型 manifest 缺失或哈希不一致"})
        if (self.root / "STOP").exists():
            alerts.append({"level": "warning", "message": "Paper STOP 开关已启用"})
        if process.get("exit_code") not in (None, 0):
            alerts.append({"level": "error", "message": "最近调度进程失败，退出码 " + str(process["exit_code"])})
        if ledger.get("status") in {"invalid", "unavailable", "missing_first_session", "stale"}:
            alerts.append({"level": "error", "message": ledger.get("message", "Paper 账本无法校验")})
        if failure:
            # Preserve historical failures, without presenting them as current failures after recovery.
            alerts.append({"level": "history", "message": "历史失败记录（请比较时间）", **pick(failure, "observed_at error_type error")})
        return clean({"schema_version": 1, "observed_at": self.now.isoformat(), "mode": "paper", "read_only": True,
                      "deployment": {**pick(dep, "start_date required_operating_days model_manifest_sha256"),
                                     **pick(model_manifest, "model_id model_type feature_version model_config_hash"),
                                     "manifest_matches": digest is not None and digest == dep.get("model_manifest_sha256"),
                                     "snapshot_end": attestation.get("end_date"), "operation_root": str(self.root)},
                      "progress": progress, "latest": pick(latest, "status date operational start_date predictions targets run_id model_id"),
                      "process": pick(process, "started_at finished_at exit_code cpu_affinity"),
                      "events": events, "logs": logs, "research": research, "paper": ledger,
                      "sessions": sessions, "alerts": alerts, "issues": self.issues}, self.secrets)
