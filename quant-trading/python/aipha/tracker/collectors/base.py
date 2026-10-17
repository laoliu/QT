"""Bounded public-page collection with explicit evidence quality.

Public profile metadata is not verified investment performance. No collector
infers returns from page titles or substitutes sample numbers for missing data.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from aipha.tracker.evidence import EvidenceQuality, EvidenceSnapshot, FailureType


@dataclass
class CollectorResult:
    platform_uid: str
    success: bool = False
    display_name: str = ""
    profile_url: str = ""
    portfolio_name: str = ""
    portfolio_url: str = ""
    asset_classes: list[str] = field(default_factory=list)
    strategy_tags: list[str] = field(default_factory=list)
    last_update_seen: str = ""
    perf_summary: dict[str, Any] = field(default_factory=dict)
    holdings: list[dict[str, Any]] | None = None
    data_sources: dict[str, str] = field(default_factory=dict)
    followers_copiers: int = 0
    strategy_description: str = ""
    failure_type: FailureType = FailureType.FIELDS_MISSING
    error_detail: str = ""
    evidence_quality: EvidenceQuality = EvidenceQuality.FAILED
    response_status: int = 0
    response_time_ms: int = 0
    raw_excerpt: str = ""

    def to_evidence_snapshot(self, account_id: str) -> EvidenceSnapshot:
        return EvidenceSnapshot(
            account_id=account_id, platform_uid=self.platform_uid,
            source_url=self.profile_url, raw_excerpt=self.raw_excerpt,
            parsed_fields={"display_name": self.display_name, "perf_summary": self.perf_summary,
                           "holdings": self.holdings, "data_sources": self.data_sources},
            failure_type=self.failure_type if not self.success else FailureType.NONE,
            error_detail=self.error_detail, evidence_quality=self.evidence_quality,
            response_status=self.response_status, response_time_ms=self.response_time_ms,
        )


class _ProfileParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.name = ""
        self.description = ""
        self.account_type = ""

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        attributes = dict(attrs)
        key = attributes.get("property", "")
        content = attributes.get("content", "")
        if key == "og:title":
            self.name = content.strip()
        elif key == "og:description":
            self.description = content.strip()
        elif key == "og:type":
            self.account_type = content.lower()


def _fetch(url: str) -> tuple[int, str, str]:
    request = Request(url, headers={"User-Agent": "Aipha-Research/0.2"})
    with urlopen(request, timeout=15) as response:
        raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("Profile response exceeds size limit")
        charset = response.headers.get_content_charset() or "utf-8"
        return response.status, raw.decode(charset, errors="replace"), response.url


class LibraryCollector:
    """Collect identified profile metadata; authenticated metrics stay absent.

    A platform page must declare og:type=profile to be accepted. Generic home,
    login, challenge and error pages never become successful account evidence.
    The injectable fetcher allows reproducible tests without live network calls.
    """

    platform_name = ""
    profile_template = ""

    def __init__(self, fetcher: Callable | None = None) -> None:
        self._fetcher = fetcher or _fetch

    async def fetch_account_info(self, platform_uid: str) -> CollectorResult:
        if not platform_uid or len(platform_uid) > 200:
            raise ValueError("Invalid platform account identifier")
        url = self.profile_template.format(uid=quote(platform_uid, safe=""))
        result = CollectorResult(platform_uid=platform_uid, profile_url=url)
        try:
            status, text, final_url = await asyncio.to_thread(self._fetcher, url)
            result.response_status = status
            final = urlsplit(final_url)
            if final.hostname != urlsplit(url).hostname or any(
                word in final.path.lower() for word in ("login", "signin", "passport")
            ):
                result.failure_type = FailureType.LOGIN_REQUIRED
                return result
            if status != 200:
                result.failure_type = self._failure(status)
                return result
            if final.path.rstrip("/") != urlsplit(url).path.rstrip("/"):
                result.error_detail = "Response redirected away from the requested account"
                return result
            parser = _ProfileParser()
            parser.feed(text)
            if parser.account_type != "profile" or not parser.name:
                result.error_detail = "Identifiable public profile metadata is absent"
                return result
            result.display_name = parser.name
            result.strategy_description = parser.description
            result.raw_excerpt = f"og:type=profile; og:title={parser.name[:300]}"
            result.success = True
            result.failure_type = FailureType.NONE
            result.evidence_quality = EvidenceQuality.PARTIAL
        except HTTPError as exc:
            result.response_status = exc.code
            result.failure_type = self._failure(exc.code)
        except (TimeoutError, asyncio.TimeoutError):
            result.failure_type = FailureType.TIMEOUT
        except URLError as exc:
            result.failure_type = (
                FailureType.TIMEOUT if isinstance(exc.reason, TimeoutError) else FailureType.NETWORK_ERROR
            )
        except (ValueError, UnicodeError):
            result.failure_type = FailureType.PARSE_ERROR
        return result

    @staticmethod
    def _failure(status: int) -> FailureType:
        return {
            401: FailureType.LOGIN_REQUIRED, 403: FailureType.WAF_BLOCKED,
            404: FailureType.LINK_DEAD, 410: FailureType.ACCOUNT_DELETED,
            429: FailureType.RATE_LIMITED,
        }.get(status, FailureType.NETWORK_ERROR)
