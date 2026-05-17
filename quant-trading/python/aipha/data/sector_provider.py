"""板块数据聚合 Provider

多源级联板块/行业数据获取:
XTData → Eastmoney → AKShare → TuShare

提供:
- 行业板块成员列表
- 概念板块成员列表
- 板块资金流向

使用示例::

    provider = SectorProvider()
    members = provider.get_industry_members("银行")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SectorType(str, Enum):
    """板块类型"""

    INDUSTRY = "industry"       # 行业板块
    CONCEPT = "concept"         # 概念板块
    AREA = "area"               # 地域板块


@dataclass
class SectorInfo:
    """板块信息"""

    name: str
    code: str
    sector_type: SectorType
    member_count: int = 0
    change_pct: float = 0.0
    source: str = ""


@dataclass
class SectorMembers:
    """板块成员"""

    sector_name: str
    sector_code: str
    sector_type: SectorType
    members: list[str] = field(default_factory=list)
    source: str = ""


class SectorProvider:
    """多源级联板块数据 Provider

    按以下优先级获取:
    1. XTData (本地 QMT 数据, 最快)
    2. Eastmoney (在线 API)
    3. AKShare (备用)
    4. TuShare (最后备选)
    """

    def __init__(self) -> None:
        self._xt: Optional[object] = None
        self._em: Optional[object] = None
        self._sources_initialized = False

    def _ensure_sources(self) -> None:
        """延迟初始化数据源"""
        if self._sources_initialized:
            return
        self._sources_initialized = True
        try:
            from aipha.data.xtdata_provider import XTDataProvider
            xt = XTDataProvider()
            if xt.is_available():
                self._xt = xt
        except Exception:
            pass
        try:
            from aipha.data.eastmoney_provider import EastmoneyProvider
            self._em = EastmoneyProvider()
        except Exception:
            pass

    # ----------------------------------------------------------
    # 行业板块
    # ----------------------------------------------------------

    def get_industry_members(self, name: str) -> Optional[SectorMembers]:
        """获取行业板块成员列表 (级联多源)"""
        self._ensure_sources()
        # 1) XTData
        if self._xt is not None:
            try:
                from aipha.data.xtdata_provider import XTDataProvider
                xt: XTDataProvider = self._xt  # type: ignore[assignment]
                members = xt.get_sector_members(name)
                if members:
                    return SectorMembers(
                        sector_name=name,
                        sector_code=name,
                        sector_type=SectorType.INDUSTRY,
                        members=members,
                        source="xtdata",
                    )
            except Exception:
                logger.debug("xtdata sector failed: %s", name)
        # 2) Eastmoney
        if self._em is not None:
            try:
                from aipha.data.eastmoney_provider import EastmoneyProvider
                em: EastmoneyProvider = self._em  # type: ignore[assignment]
                members = em.get_sector_members(name)
                if members:
                    return SectorMembers(
                        sector_name=name,
                        sector_code=name,
                        sector_type=SectorType.INDUSTRY,
                        members=members,
                        source="eastmoney",
                    )
            except Exception:
                logger.debug("eastmoney sector failed: %s", name)
        # 3) AKShare fallback
        try:
            import akshare as ak
            df = ak.stock_board_industry_cons_em(symbol=name)
            if df is not None and not df.empty:
                codes = df["代码"].tolist()
                return SectorMembers(
                    sector_name=name,
                    sector_code=name,
                    sector_type=SectorType.INDUSTRY,
                    members=codes,
                    source="akshare",
                )
        except Exception:
            logger.debug("akshare sector failed: %s", name)
        return None

    # ----------------------------------------------------------
    # 概念板块
    # ----------------------------------------------------------

    def get_concept_members(self, name: str) -> Optional[SectorMembers]:
        """获取概念板块成员列表"""
        self._ensure_sources()
        try:
            import akshare as ak
            df = ak.stock_board_concept_cons_em(symbol=name)
            if df is not None and not df.empty:
                codes = df["代码"].tolist()
                return SectorMembers(
                    sector_name=name,
                    sector_code=name,
                    sector_type=SectorType.CONCEPT,
                    members=codes,
                    source="akshare",
                )
        except Exception:
            logger.debug("concept sector failed: %s", name)
        return None
