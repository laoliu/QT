"""Public ZuluTrade leader statistics collector."""

import asyncio
import json
import math
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit

from .base import CollectorResult, LibraryCollector
from ..evidence import EvidenceQuality, FailureType


class ZuluTradeCollector(LibraryCollector):
    platform_name = "zulutrade"
    profile_template = "https://www.zulutrade.com/trader/{uid}"

    stats_endpoint = (
        "https://www.zulutrade.com/zulutrade-gateway/"
        "api/providers/performance/topTraders/search"
    )

    async def fetch_account_info(self, platform_uid: str) -> CollectorResult:
        if not re.fullmatch(r"\d{1,12}", platform_uid):
            raise ValueError("ZuluTrade provider ID must be numeric")
        result = CollectorResult(
            platform_uid=platform_uid,
            profile_url=self.profile_template.format(uid=quote(platform_uid, safe="")),
        )
        try:
            status, text, final_url = await asyncio.to_thread(self._fetcher, self.stats_endpoint)
            result.response_status = status
            if status != 200:
                result.failure_type = self._failure(status)
                return result
            expected, final = urlsplit(self.stats_endpoint), urlsplit(final_url)
            if final.hostname != expected.hostname or final.path.rstrip("/") != expected.path.rstrip("/"):
                result.error_detail = "Leader search redirected away from the expected endpoint"
                result.failure_type = FailureType.PARSE_ERROR
                return result
            payload = json.loads(text)
            entries = payload[0]["result"] if isinstance(payload, list) and payload else []
            match = next(
                (entry.get("trader") for entry in entries
                 if str(entry.get("trader", {}).get("providerId", "")) == platform_uid),
                None,
            )
            if not isinstance(match, dict):
                result.error_detail = "Provider is not present in the public leader search response"
                result.failure_type = FailureType.FIELDS_MISSING
                return result
            profile = match.get("profile") or {}
            stats = match.get("overallStats") or {}
            timeframe_stats = match.get("timeframeStats") or {}
            timeframe_id, timeframe = next(iter(timeframe_stats.items()), ("", {}))
            if profile.get("public") is not True:
                result.error_detail = "Provider is not marked public by ZuluTrade"
                result.failure_type = FailureType.FIELDS_MISSING
                return result

            def number(mapping, key):
                value = mapping.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"Invalid ZuluTrade metric: {key}")
                return value

            followers = number(stats, "followers")
            if not float(followers).is_integer():
                raise ValueError("Invalid ZuluTrade follower count")
            summary = {
                "roi_profit_pct": number(stats, "roiProfit"),
                "roi_annualized_pct": number(stats, "roiAnnualized"),
                "drawdown_pct": number(stats, "overallDrawDownPercent"),
                "followers": int(followers),
                "weeks": int(number(stats, "weeks")),
                "timeframe": {
                    "provider_timeframe_id": str(timeframe_id),
                    "profit": number(timeframe, "profit"),
                    "max_drawdown_pct": number(timeframe, "maxDrawDownPercent"),
                    "sharpe": number(timeframe, "timeFrameSharpeRatio"),
                },
                "account_type": "demo" if profile.get("demo") is True else "real_or_unspecified",
            }
            result.success = True
            result.display_name = str(profile.get("name") or platform_uid)
            result.strategy_description = str(profile.get("strategyDesc") or "")
            result.failure_type = FailureType.NONE
            result.evidence_quality = EvidenceQuality.PARTIAL
            result.perf_summary = summary
            result.followers_copiers = int(followers)
            last_updated = stats.get("lastUpdatedDate")
            if isinstance(last_updated, (int, float)) and not isinstance(last_updated, bool) and math.isfinite(last_updated) and last_updated > 0:
                result.last_update_seen = datetime.fromtimestamp(
                    last_updated / 1000, timezone.utc,
                ).date().isoformat()
            result.data_sources["performance"] = self.stats_endpoint
            result.raw_excerpt = json.dumps({"providerId": platform_uid, "stats": summary})
            return result
        except HTTPError as exc:
            result.response_status = exc.code
            result.failure_type = self._failure(exc.code)
        except (TimeoutError, asyncio.TimeoutError):
            result.failure_type = FailureType.TIMEOUT
        except URLError as exc:
            result.failure_type = (
                FailureType.TIMEOUT if isinstance(exc.reason, TimeoutError) else FailureType.NETWORK_ERROR
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            result.failure_type = FailureType.PARSE_ERROR
            return result
