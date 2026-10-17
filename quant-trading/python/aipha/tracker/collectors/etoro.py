"""Public eToro return history and current aggregated portfolio data."""

import asyncio
import json
import math
import re
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit

from .base import CollectorResult, LibraryCollector
from ..evidence import EvidenceQuality, FailureType


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid numeric field")
    return value


def _returns(rows):
    if not isinstance(rows, list):
        raise ValueError("Return history is absent")
    result = []
    dates = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("isSimulation"), bool):
            raise ValueError("Simulation provenance is absent")
        if row["isSimulation"]:
            continue
        if not isinstance(row.get("start"), str):
            raise ValueError("Return timestamp is absent")
        start = datetime.fromisoformat(row["start"].replace("Z", "+00:00"))
        gain = _number(row["gain"])
        if start.tzinfo is None or gain < -100 or start in dates:
            raise ValueError("Invalid return period")
        dates.add(start)
        result.append({"start": start.isoformat(), "gain_pct": gain})
    return sorted(result, key=lambda row: row["start"])


class EtoroCollector(LibraryCollector):
    platform_name = "etoro"
    profile_template = "https://www.etoro.com/people/{uid}"

    async def _json(self, url):
        status, text, final_url = await asyncio.to_thread(self._fetcher, url)
        if status != 200:
            raise HTTPError(url, status, "Public API unavailable", None, None)
        if urlsplit(final_url) != urlsplit(url):
            raise ValueError("API redirected away from requested account")
        return json.loads(text)

    async def fetch_account_info(self, platform_uid: str) -> CollectorResult:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", platform_uid):
            raise ValueError("Invalid eToro username")
        history_url = (
            "https://www.etoro.com/sapi/userstats/gain/username/"
            f"{quote(platform_uid, safe='')}/history?IncludeSimulation=false"
        )
        try:
            data = await self._json(history_url)
            customer_id = data["customerId"]
            if type(customer_id) is not int or customer_id <= 0:
                raise ValueError("Invalid account identity")
            monthly = _returns(data["monthly"])
            if not monthly:
                raise ValueError("No non-simulated monthly returns")
            yearly = _returns(data.get("yearly", []))
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError):
            # Existing metadata-only behavior remains available when metrics cannot
            # be verified; never invent performance from an HTML profile.
            return await super().fetch_account_info(platform_uid)

        result = CollectorResult(
            platform_uid=platform_uid, display_name=platform_uid, success=True,
            profile_url=self.profile_template.format(uid=platform_uid),
            response_status=200, failure_type=FailureType.NONE,
            evidence_quality=EvidenceQuality.PARTIAL,
            last_update_seen=monthly[-1]["start"][:10],
            perf_summary={"monthly_returns_pct": monthly, "yearly_returns_pct": yearly,
                          "customer_id": customer_id, "includes_simulation": False},
            data_sources={"returns": history_url},
            raw_excerpt=json.dumps({"customerId": customer_id, "last_month": monthly[-1]}),
        )
        portfolio_url = (
            "https://www.etoro.com/sapi/trade-data-real/live/public/portfolios"
            f"?cid={customer_id}"
        )
        try:
            portfolio = await self._json(portfolio_url)
            rows = portfolio["AggregatedPositions"]
            if not isinstance(rows, list):
                raise ValueError("Invalid aggregated portfolio")
            holdings = []
            for row in rows:
                instrument = row["InstrumentID"]
                if type(instrument) is not int or instrument <= 0 or row["Direction"] not in ("Buy", "Sell"):
                    raise ValueError("Invalid portfolio instrument")
                holdings.append({"instrument_id": instrument, "direction": row["Direction"],
                                 "source_fields": {key: _number(row[key])
                                                   for key in ("Invested", "NetProfit", "Value")}})
            result.holdings = holdings
            result.data_sources["holdings"] = portfolio_url
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError):
            result.error_detail = "Return history available; current holdings could not be verified"
        return result
