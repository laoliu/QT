from __future__ import annotations

from typing import Any

from aipha.tracker.evidence import EvidenceQuality, FailureType

from .base import CollectorResult, LibraryCollector


class XueqiuLibraryCollector(LibraryCollector):
    platform_name = "xueqiu"
    profile_template = "https://xueqiu.com/u/{uid}"


class XueqiuAuthenticatedCollector:
    """将已有的 Playwright 登录态适配到统一 evidence collector。

    不创建或启动浏览器，也不从持仓推算收益。调用方必须显式提供已启动
    的 session、组合代码和作者信息。
    """

    platform_name = "xueqiu"

    def __init__(
        self,
        session: Any,
        portfolio_codes: list[str] | tuple[str, ...],
        *,
        author_id: str = "",
        author_name: str = "",
        collector_cls: type | None = None,
    ) -> None:
        if session is None:
            raise ValueError("An already-started BrowserSession is required")
        self._portfolio_codes = tuple(
            str(code).strip() for code in portfolio_codes if str(code).strip()
        )
        self._author_id = author_id
        self._author_name = author_name
        if collector_cls is None:
            from aipha.tracker.browser.xueqiu import XueqiuCollector

            collector_cls = XueqiuCollector
        self._collector = collector_cls(session)

    async def fetch_account_info(self, platform_uid: str) -> CollectorResult:
        """采集作者配置下的组合持仓，失败时返回可审计结果。"""
        result = CollectorResult(
            platform_uid=platform_uid,
            display_name=self._author_name,
            profile_url=f"https://xueqiu.com/u/{platform_uid}",
        )
        if not platform_uid or len(platform_uid) > 200:
            result.failure_type = FailureType.PARSE_ERROR
            result.error_detail = "Invalid platform account identifier"
            return result
        if not self._portfolio_codes:
            result.error_detail = "No authenticated portfolio codes configured"
            return result
        try:
            if not await self._collector.check_login():
                result.failure_type = FailureType.LOGIN_REQUIRED
                result.error_detail = "Xueqiu login is missing or expired"
                return result
        except Exception as exc:
            result.failure_type = FailureType.LOGIN_EXPIRED
            result.error_detail = f"Unable to verify Xueqiu login: {exc}"
            return result

        snapshots = []
        for code in self._portfolio_codes:
            snapshot = await self._collector.fetch_portfolio(
                portfolio_code=code,
                author_id=self._author_id or platform_uid,
                author_name=self._author_name or platform_uid,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
        if not snapshots:
            result.error_detail = "No authenticated Xueqiu portfolio data returned"
            return result

        holdings: list[dict[str, Any]] = []
        sources: dict[str, str] = {}
        for snapshot in snapshots:
            code = snapshot.portfolio_code or ""
            sources[f"holdings:{code}"] = f"https://xueqiu.com/P/{code}"
            for holding in snapshot.holdings:
                item = holding.to_dict()
                item["portfolio_code"] = code
                holdings.append(item)

        result.success = True
        result.failure_type = FailureType.NONE
        result.evidence_quality = EvidenceQuality.PARTIAL
        result.portfolio_name = self._author_name
        result.portfolio_url = next(iter(sources.values()), "")
        result.holdings = holdings
        result.data_sources = sources
        result.raw_excerpt = f"authenticated=xueqiu; portfolios={','.join(sources)}"
        # 持仓不是收益时间序列，绝不从中推导 perf_summary。
        return result
