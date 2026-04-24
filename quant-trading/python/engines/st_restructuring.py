"""
ST Restructuring Tracking Engine — 破产重整跟踪引擎

Tracks ST stocks through the restructuring lifecycle by scraping announcements
from Eastmoney (via akshare) and classifying each stock's current stage.

Restructuring lifecycle stages:
  PRE-0  预重整申请        (Pre-restructuring application filed)
  PRE-1  法院受理预重整     (Court accepts pre-restructuring)
  PRE-2  管理人指定         (Administrator appointed)
  PRE-3  040779           (Creditor claims filing)
  PRE-4  0404         (Investor recruitment)
  PRE-5  0504       (Investment agreement signed)
  PRE-6  预重整报告提交     (Pre-restructuring report submitted)
  MAIN-1 法院裁定受理重整   (Court accepts formal restructuring)
  MAIN-2 040779(04)    (Formal creditor claims filing)
  MAIN-3 0718       (Restructuring plan draft)
  MAIN-4 分组表决           (Group voting)
  MAIN-5 020607       (Court approves plan)
  MAIN-6 0407       (Executing restructuring plan)
  MAIN-7 0204       (Court rules execution complete)
  REMOVED 0009/0005 (Risk warning removed)

Input (JSON via stdin):
  {
    "action": "scan_all" | "track_stock" | "status",
    "symbol": "600165",
    "days": 90,
    "force_refresh": false
  }

Output: JSON with restructuring tracking data.
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


# ── Data directory for cache & state ──────────────────────────────────────────
_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache")
_STATE_FILE = os.path.join(_CACHE_DIR, "st_restructuring_state.json")

# ── Stage definitions ─────────────────────────────────────────────────────────

STAGES = {
    "PRE-0":   "预重整申请",
    "PRE-1":   "法院受理预重整",
    "PRE-2":   "管理人指定",
    "PRE-3":   "债权申报",
    "PRE-4":   "招募投资人",
    "PRE-5":   "投资协议签署",
    "PRE-6":   "预重整报告提交",
    "MAIN-1":  "法院裁定受理",
    "MAIN-2":  "正式债权申报",
    "MAIN-3":  "重整计划草案",
    "MAIN-4":  "分组表决",
    "MAIN-5":  "法院批准计划",
    "MAIN-6":  "执行重整计划",
    "MAIN-7":  "执行完毕",
    "REMOVED": "撤销风险警示",
    "UNKNOWN": "未知",
}

STAGE_ORDER = list(STAGES.keys())

# ── Keyword → stage classification patterns ───────────────────────────────────
# Ordered from most specific to least: first match wins.
_STAGE_PATTERNS: list[tuple[str, list[str]]] = [
    ("REMOVED", ["撤销其他风险警示", "撤销退市风险警示", "撤销.*风险警示"]),
    ("MAIN-7", ["重整计划执行完毕", "重整执行完毕", "执行完毕"]),
    ("MAIN-6", ["执行重整计划", "资本公积金转增", "重整.*转增",
                "执行.*重整计划"]),
    ("MAIN-5", ["法院.*批准.*重整计划", "裁定批准.*重整计划", "批准重整计划"]),
    ("MAIN-4", ["债权人.*表决", "分组表决", "表决.*重整计划"]),
    ("MAIN-3", ["重整计划.*草案", "重整计划草案"]),
    ("MAIN-2", ["正式.*债权申报", "债权申报.*公告", "债权.*申报"]),
    ("MAIN-1", ["裁定受理.*重整", "受理重整申请", "法院.*受理.*重整",
                "裁定.*受理.*重整"]),
    ("PRE-6", ["预重整.*报告", "预重整工作报告"]),
    ("PRE-5", ["投资协议.*签署", "签署.*投资协议", "投资.*协议"]),
    ("PRE-4", ["招募.*投资人", "重整.*招募.*投资", "公开招募"]),
    ("PRE-3", ["债权.*申报", "申报债权"]),
    ("PRE-2", ["指定.*管理人", "管理人.*指定", "临时管理人"]),
    ("PRE-1", ["受理.*预重整", "预重整.*受理"]),
    ("PRE-0", ["申请.*预重整", "预重整.*申请", "破产重整.*申请",
               "申请.*破产重整", "破产重整"]),
]


@dataclass
class RestructuringRecord:
    """Tracks a single stock's restructuring lifecycle."""
    code: str
    name: str
    current_stage: str = "UNKNOWN"
    stage_label: str = ""
    first_seen: str = ""
    last_updated: str = ""
    days_in_process: int = 0
    announcements: list[dict[str, str]] = field(default_factory=list)
    stage_history: list[dict[str, str]] = field(default_factory=list)
    is_st: bool = False
    risk_level: str = ""  # "delisting", "st", "normal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _classify_stage(title: str) -> Optional[str]:
    """Classify announcement title into a restructuring stage."""
    # Strip HTML tags (cninfo returns <em>重整</em> highlights)
    clean = re.sub(r"<[^>]+>", "", title)
    for stage, patterns in _STAGE_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, clean):
                return stage
    return None


def _load_state() -> dict[str, dict]:
    """Load persisted tracking state."""
    if os.path.exists(_STATE_FILE):
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict[str, dict]) -> None:
    """Persist tracking state."""
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _fetch_announcements(days: int = 90, categories: Optional[list[str]] = None) -> list[dict]:
    """Fetch restructuring-related announcements from Eastmoney.

    Scans multiple announcement categories and filters for restructuring keywords.
    """
    import akshare as ak
    import pandas as pd

    # "全部" already includes all other categories — no need to query each one
    if categories is None:
        categories = ["全部"]

    today = datetime.now()
    all_announcements: list[dict] = []
    seen_urls: set[str] = set()

    # Sample dates across the time window (API returns one page per date)
    sample_dates: list[str] = []
    for i in range(0, days, 7):  # every 7 days (restructuring events are rare)
        d = today - timedelta(days=i)
        sample_dates.append(d.strftime("%Y%m%d"))

    restructuring_keywords = re.compile(
        r"重整|破产重整|重整计划|债权申报|管理人|"
        r"撤销.*风险警示|重整投资"
    )

    for date_str in sample_dates:
        for cat in categories:
            try:
                df = ak.stock_notice_report(symbol=cat, date=date_str)
                if df is None or df.empty:
                    continue

                for _, row in df.iterrows():
                    title = str(row.get("公告标题", ""))
                    url = str(row.get("公告链接", row.get("公告网址", "")))
                    if url in seen_urls:
                        continue

                    if restructuring_keywords.search(title):
                        seen_urls.add(url)
                        all_announcements.append({
                            "code": str(row.get("代码", "")),
                            "name": str(row.get("名称", "")),
                            "title": title,
                            "category": str(row.get("公告类型", "")),
                            "date": str(row.get("公告日期", "")),
                            "url": url,
                        })
            except (Exception, KeyboardInterrupt):
                continue

    # Sort by date descending
    all_announcements.sort(key=lambda x: x.get("date", ""), reverse=True)
    return all_announcements


def _fetch_stock_announcements(symbol: str, days: int = 365) -> list[dict]:
    """Fetch announcements for a specific stock via cninfo disclosure API + news."""
    import akshare as ak

    results: list[dict] = []
    restructuring_keywords = re.compile(
        r"重整|破产重整|重整计划|债权申报|管理人|"
        r"撤销.*风险警示|重整投资"
    )

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    # Method 1: cninfo disclosure API (best for per-stock search w/ keyword)
    for kw in ["重整", "破产重整", "风险警示"]:
        try:
            df = ak.stock_zh_a_disclosure_report_cninfo(
                symbol=symbol,
                market="80",
                keyword=kw,
                start_date=start_date,
                end_date=end_date,
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    title = str(row.get("公告标题", row.get("announcementTitle", "")))
                    if restructuring_keywords.search(title):
                        results.append({
                            "code": symbol,
                            "name": str(row.get("证券简称", row.get("secName", ""))),

                            "title": title,
                            "category": "cninfo",
                            "date": str(row.get("公告时间", row.get("announcementTime", "")))[:10],
                            "url": str(row.get("公告网址", row.get("adjunctUrl", ""))),
                        })
        except Exception:
            pass

    # Method 2: Eastmoney stock news (supplementary)
    try:
        df = ak.stock_news_em(symbol=symbol)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                title = str(row.get("新闻标题", ""))
                if restructuring_keywords.search(title):
                    results.append({
                        "code": symbol,
                        "name": "",
                        "title": title,
                        "category": "stock_news",
                        "date": str(row.get("发布时间", ""))[:10],
                        "url": str(row.get("新闻链接", "")),
                    })
    except Exception:
        pass

    return results


def scan_all_restructuring(days: int = 90, force_refresh: bool = False) -> dict[str, RestructuringRecord]:
    """Scan announcement feed for all stocks with restructuring activity.

    Returns dict of code → RestructuringRecord.
    """
    state = _load_state() if not force_refresh else {}
    announcements = _fetch_announcements(days=days)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Group announcements by stock code
    by_stock: dict[str, list[dict]] = {}
    for ann in announcements:
        code = ann["code"]
        if code not in by_stock:
            by_stock[code] = []
        by_stock[code].append(ann)

    records: dict[str, RestructuringRecord] = {}

    for code, anns in by_stock.items():
        name = anns[0].get("name", "")
        is_st = "ST" in name or "退" in name

        # Determine current stage from most recent announcement
        current_stage = "UNKNOWN"
        for ann in anns:  # already sorted by date desc
            stage = _classify_stage(ann["title"])
            if stage:
                current_stage = stage
                break

        # Build stage history from all announcements
        stage_history: list[dict[str, str]] = []
        for ann in reversed(anns):  # oldest first
            stage = _classify_stage(ann["title"])
            if stage:
                stage_history.append({
                    "stage": stage,
                    "label": STAGES.get(stage, ""),
                    "date": ann["date"],
                    "title": ann["title"],
                })

        # Calculate days in process
        dates = [a["date"] for a in anns if a.get("date")]
        first_seen = min(dates) if dates else today_str
        days_in_process = (datetime.now() - datetime.strptime(first_seen[:10], "%Y-%m-%d")).days

        # Risk level
        risk_level = "normal"
        if "退" in name:
            risk_level = "delisting"
        elif "*ST" in name:
            risk_level = "delisting_risk"
        elif "ST" in name:
            risk_level = "st"

        # Skip stocks where no announcement matched a restructuring stage —
        # these are false positives from the broad keyword search.
        if current_stage == "UNKNOWN":
            continue

        record = RestructuringRecord(
            code=code,
            name=name,
            current_stage=current_stage,
            stage_label=STAGES.get(current_stage, "未知"),
            first_seen=first_seen,
            last_updated=today_str,
            days_in_process=days_in_process,
            announcements=[{
                "title": a["title"],
                "date": a["date"],
                "url": a.get("url", ""),
            } for a in anns[:20]],  # keep last 20 announcements
            stage_history=stage_history,
            is_st=is_st,
            risk_level=risk_level,
        )
        records[code] = record

        # Merge into persisted state
        state[code] = record.to_dict()

    _save_state(state)
    return records


def track_stock(symbol: str, days: int = 365) -> RestructuringRecord:
    """Deep-track a specific stock's restructuring timeline using cninfo API."""
    anns = _fetch_stock_announcements(symbol, days=days)

    # Deduplicate by title+date
    seen: set[str] = set()
    unique_anns: list[dict] = []
    for a in anns:
        key = f"{a['title']}_{a['date']}"
        if key not in seen:
            seen.add(key)
            unique_anns.append(a)

    unique_anns.sort(key=lambda x: x.get("date", ""), reverse=True)
    name = unique_anns[0].get("name", "") if unique_anns else ""

    # Determine stage
    current_stage = "UNKNOWN"
    for ann in unique_anns:
        stage = _classify_stage(ann["title"])
        if stage:
            current_stage = stage
            break

    stage_history = []
    for ann in reversed(unique_anns):
        stage = _classify_stage(ann["title"])
        if stage:
            stage_history.append({
                "stage": stage,
                "label": STAGES.get(stage, ""),
                "date": ann["date"],
                "title": ann["title"],
            })

    dates = [a["date"] for a in unique_anns if a.get("date")]
    first_seen = min(dates) if dates else datetime.now().strftime("%Y-%m-%d")
    days_in_process = (datetime.now() - datetime.strptime(first_seen[:10], "%Y-%m-%d")).days

    is_st = "ST" in name or "退" in name
    risk_level = "normal"
    if "退" in name:
        risk_level = "delisting"
    elif "*ST" in name:
        risk_level = "delisting_risk"
    elif "ST" in name:
        risk_level = "st"

    return RestructuringRecord(
        code=symbol,
        name=name,
        current_stage=current_stage,
        stage_label=STAGES.get(current_stage, "未知"),
        first_seen=first_seen,
        last_updated=datetime.now().strftime("%Y-%m-%d"),
        days_in_process=days_in_process,
        announcements=[{
            "title": a["title"],
            "date": a["date"],
            "url": a.get("url", ""),
        } for a in unique_anns[:30]],
        stage_history=stage_history,
        is_st=is_st,
        risk_level=risk_level,
    )


def get_status() -> dict:
    """Get current tracking status from persisted state."""
    state = _load_state()

    # Summarize by stage
    by_stage: dict[str, list[dict]] = {}
    for code, record in state.items():
        stage = record.get("current_stage", "UNKNOWN")
        if stage not in by_stage:
            by_stage[stage] = []
        by_stage[stage].append({
            "code": code,
            "name": record.get("name", ""),
            "stage_label": record.get("stage_label", ""),
            "days_in_process": record.get("days_in_process", 0),
            "last_updated": record.get("last_updated", ""),
            "risk_level": record.get("risk_level", ""),
        })

    # Stocks at MAIN-6 (executing plan) or MAIN-7 (completed)
    executing = by_stage.get("MAIN-6", [])
    completed = by_stage.get("MAIN-7", [])
    removed = by_stage.get("REMOVED", [])

    return {
        "total_tracked": len(state),
        "by_stage": by_stage,
        "executing_plan": executing,
        "completed": completed,
        "risk_removed": removed,
        "stage_summary": {
            stage: len(stocks) for stage, stocks in by_stage.items()
        },
    }


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main() -> None:
    try:
        raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
        config = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, EOFError):
        config = {}

    action = config.get("action", "status")

    try:
        if action == "scan_all":
            days = config.get("days", 90)
            force = config.get("force_refresh", False)
            records = scan_all_restructuring(days=days, force_refresh=force)
            result = {
                "action": "scan_all",
                "total": len(records),
                "records": {code: r.to_dict() for code, r in records.items()},
            }

        elif action == "track_stock":
            symbol = config.get("symbol", "")
            if not symbol:
                result = {"error": "symbol is required"}
            else:
                days = config.get("days", 365)
                record = track_stock(symbol, days=days)
                result = {
                    "action": "track_stock",
                    "record": record.to_dict(),
                }

        elif action == "status":
            result = get_status()

        else:
            result = {"error": f"unknown action: {action}"}

        print(json.dumps(result, ensure_ascii=False, indent=2))

    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
