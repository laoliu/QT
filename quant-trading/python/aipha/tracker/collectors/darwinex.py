"""Public Darwinex DARWIN performance page collector."""

import asyncio
import math
import re
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from .base import CollectorResult, LibraryCollector
from ..evidence import EvidenceQuality, FailureType


class _DarwinPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.darwin = ""
        self.values: dict[str, list[str]] = {}
        self._capture: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if "header-main__darwin-name" in classes:
            self._capture = "darwin"
            self._parts = []
        for key in ("js-return-total", "js-return-annualized"):
            if key in classes:
                self._capture = key
                self._parts = []

    def handle_data(self, data):
        if self._capture:
            self._parts.append(data.strip())

    def handle_endtag(self, tag):
        if tag not in ("span", "div") or not self._capture:
            return
        value = " ".join(part for part in self._parts if part).strip()
        if self._capture == "darwin":
            self.darwin = value
        else:
            self.values.setdefault(self._capture, []).append(value)
        self._capture = None
        self._parts = []


class DarwinexCollector(LibraryCollector):
    platform_name = "darwinex"
    profile_template = "https://www.darwinex.com/darwin/{uid}"

    async def fetch_account_info(self, platform_uid: str) -> CollectorResult:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", platform_uid):
            raise ValueError("Invalid Darwinex DARWIN code")
        url = self.profile_template.format(uid=quote(platform_uid, safe=""))
        result = CollectorResult(platform_uid=platform_uid, profile_url=url)
        try:
            status, html, final_url = await asyncio.to_thread(self._fetcher, url)
            result.response_status = status
            if status != 200:
                result.failure_type = self._failure(status)
                return result
            final = urlsplit(final_url)
            parts = [part for part in final.path.split("/") if part]
            if len(parts) < 2 or parts[-1].casefold() != platform_uid.casefold() or parts[-2] != "invest":
                result.error_detail = "Response did not identify the requested DARWIN"
                return result

            parser = _DarwinPage()
            parser.feed(html)
            if parser.darwin.casefold() != platform_uid.casefold():
                result.error_detail = "Rendered DARWIN identity does not match the requested code"
                return result

            def metric(key: str) -> float:
                values = parser.values.get(key, [])
                for raw in values:
                    if not raw:
                        continue
                    value = float(raw.replace(",", ""))
                    if math.isfinite(value):
                        return value
                raise ValueError(f"Missing public metric: {key}")

            total = metric("js-return-total")
            annualized = metric("js-return-annualized")
            if total <= -100 or annualized <= -100:
                raise ValueError("Invalid public return percentage")
            result.success = True
            result.display_name = parser.darwin
            result.failure_type = FailureType.NONE
            result.evidence_quality = EvidenceQuality.PARTIAL
            result.perf_summary = {
                "cumulative_return_pct": total,
                "annualized_return_pct": annualized,
                "holding_period_label": self._period_label(html),
            }
            result.data_sources["performance"] = final_url
            result.raw_excerpt = (
                f"DARWIN={parser.darwin}; cumulative_return_pct={total}; "
                f"annualized_return_pct={annualized}"
            )
        except (TimeoutError, UnicodeError, ValueError):
            result.failure_type = FailureType.PARSE_ERROR
        return result

    @staticmethod
    def _period_label(html: str) -> str:
        match = re.search(r"Return\s+(\d+\s+years?)", html, re.IGNORECASE)
        return match.group(1) if match else ""
