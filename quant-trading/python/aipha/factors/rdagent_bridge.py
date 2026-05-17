"""RD-Agent / Qlib 因子桥接器 — 将 RD-Agent 产出的因子导入 Aipha-Q。

功能：
1. 扫描 rdagent workspace 目录收集因子（factor.py + result.h5）
2. 转换 Qlib MultiIndex 格式 → Aipha 面板 DataFrame
3. 合并多因子为统一面板，供 ML 模型或策略消费
4. 标的代码格式转换（SH600000 ↔ 600000.SH）

使用方式：
    bridge = RDAgentFactorBridge("/path/to/rdagent/workspace")
    factors = bridge.scan_factors()
    panel = bridge.build_panel(factors)
    panel.to_parquet("data/cache/rdagent_factors.parquet")

与现有架构关系：
- 产出面板可喂给 CBLGBRanker / MLStockSelector
- 也可通过 FactorExpressionParser 注册为表达式因子
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# Qlib instrument code ↔ TuShare ts_code 转换
# Qlib: SH600000, SZ000001  →  TuShare: 600000.SH, 000001.SZ
_QLIB_PREFIX = re.compile(r"^(SH|SZ)(\d{6})$")


def qlib_to_tscode(instrument: str) -> str:
    """SH600000 → 600000.SH"""
    m = _QLIB_PREFIX.match(instrument)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    return instrument


def tscode_to_qlib(ts_code: str) -> str:
    """600000.SH → SH600000"""
    parts = ts_code.split(".")
    if len(parts) == 2 and parts[1] in ("SH", "SZ"):
        return f"{parts[1]}{parts[0]}"
    return ts_code


@dataclass
class DiscoveredFactor:
    """从 rdagent workspace 扫描到的因子描述"""
    name: str
    workspace_dir: str
    factor_py_path: str
    result_h5_path: Optional[str] = None
    description: str = ""
    formulation: str = ""
    n_rows: int = 0
    n_instruments: int = 0
    date_range: tuple[str, str] = ("", "")
    has_result: bool = False


class RDAgentFactorBridge:
    """扫描 RD-Agent 产出并转换为 Aipha 可用格式。

    支持两种目录结构：
    1. rdagent workspace: git_ignore_folder/RD-Agent_workspace/<uuid>/
    2. rdagent log: log/<timestamp>/Loop_N/coding/evo_loop_N/
    """

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)
        if not self.base_dir.exists():
            raise FileNotFoundError(f"RD-Agent directory not found: {base_dir}")

    def scan_factors(self) -> list[DiscoveredFactor]:
        """扫描 base_dir 下所有因子 workspace，返回发现的因子列表。"""
        factors: list[DiscoveredFactor] = []

        # Pattern 1: RD-Agent_workspace/<uuid>/factor.py
        ws_dir = self.base_dir / "git_ignore_folder" / "RD-Agent_workspace"
        if ws_dir.exists():
            for d in sorted(ws_dir.iterdir()):
                if d.is_dir():
                    f = self._probe_workspace(d)
                    if f:
                        factors.append(f)

        # Pattern 2: log dir 下递归搜索 factor.py
        for fpy in sorted(self.base_dir.rglob("factor.py")):
            ws = fpy.parent
            # 避免重复（已在 pattern 1 中扫描）
            if any(f.workspace_dir == str(ws) for f in factors):
                continue
            f = self._probe_workspace(ws)
            if f:
                factors.append(f)

        logger.info("Scanned %d factors from %s", len(factors), self.base_dir)
        return factors

    def _probe_workspace(self, ws_dir: Path) -> Optional[DiscoveredFactor]:
        """探测单个 workspace 目录，提取因子元数据。"""
        fpy = ws_dir / "factor.py"
        if not fpy.exists():
            return None

        # 从 factor.py 提取因子名
        name = self._extract_factor_name(fpy)
        if not name:
            return None

        # Support both result.h5 and result.parquet
        result_h5 = ws_dir / "result.h5"
        result_pq = ws_dir / "result.parquet"
        result_path = (
            str(result_h5) if result_h5.exists()
            else str(result_pq) if result_pq.exists()
            else None
        )
        factor = DiscoveredFactor(
            name=name,
            workspace_dir=str(ws_dir),
            factor_py_path=str(fpy),
            result_h5_path=result_path,
            has_result=result_path is not None,
        )

        # 如果有 result.h5，提取元数据
        if factor.has_result:
            try:
                df = self._read_result(factor.result_h5_path)
                factor.n_rows = len(df)
                idx = df.index
                if hasattr(idx, "levels") and len(idx.names) >= 2:
                    dates = idx.get_level_values(0)
                    insts = idx.get_level_values(1)
                    factor.n_instruments = insts.nunique()
                    factor.date_range = (
                        str(dates.min().date()),
                        str(dates.max().date()),
                    )
                elif "datetime" in df.columns and "instrument" in df.columns:
                    factor.n_instruments = df["instrument"].nunique()
                    factor.date_range = (
                        str(df["datetime"].min().date()),
                        str(df["datetime"].max().date()),
                    )
            except Exception as e:
                logger.warning("Failed to read %s: %s", factor.result_h5_path, e)

        return factor

    @staticmethod
    def _extract_factor_name(factor_py: Path) -> str:
        """从 factor.py 中提取因子名（def calculate_XXX → XXX）。"""
        text = factor_py.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"def\s+calculate_(\w+)\s*\(", text)
        if m:
            return m.group(1)
        # fallback: 从 to_hdf 的 column name 提取
        m = re.search(r"name=['\"](\w+)['\"]", text)
        return m.group(1) if m else ""

    @staticmethod
    def _read_result(path: str) -> pd.DataFrame:
        """读取 result.h5 或 result.parquet。"""
        if path.endswith(".parquet"):
            df = pd.read_parquet(path)
            # 还原 MultiIndex（如果有 datetime + instrument 列）
            if "datetime" in df.columns and "instrument" in df.columns:
                return df
            return df
        # HDF5
        return pd.read_hdf(path, key="data")

    # ------------------------------------------------------------------
    # 面板构建
    # ------------------------------------------------------------------

    def load_factor(
        self, factor: DiscoveredFactor, convert_codes: bool = True,
    ) -> pd.DataFrame:
        """加载单个因子的 result.h5，返回标准 DataFrame。

        输出格式：
            columns = [datetime, symbol, <factor_name>]
            datetime: pd.Timestamp
            symbol: str (TuShare 格式 600000.SH 或原始 Qlib 格式)
        """
        if not factor.has_result or not factor.result_h5_path:
            raise FileNotFoundError(
                f"No result.h5 for factor {factor.name}"
            )

        df = self._read_result(factor.result_h5_path)

        # 从 MultiIndex 转为平坦列
        if hasattr(df.index, "names") and len(df.index.names) >= 2:
            df = df.reset_index()
        # 确保有 datetime + instrument 列
        if "datetime" in df.columns and "instrument" in df.columns:
            # 保留 datetime, instrument, 和因子列
            factor_cols = [c for c in df.columns if c not in ("datetime", "instrument")]
            if len(factor_cols) == 1 and factor_cols[0] != factor.name:
                df = df.rename(columns={factor_cols[0]: factor.name})
            df = df[["datetime", "instrument", factor.name]]
        else:
            raise ValueError(
                f"Unexpected format for {factor.name}: cols={list(df.columns)}"
            )

        # 标的代码转换
        if convert_codes:
            df["symbol"] = df["instrument"].map(qlib_to_tscode)
        else:
            df["symbol"] = df["instrument"]

        df = df.drop(columns=["instrument"])
        return df[["datetime", "symbol", factor.name]]

    def build_panel(
        self,
        factors: list[DiscoveredFactor],
        convert_codes: bool = True,
    ) -> pd.DataFrame:
        """合并多个因子为统一面板 DataFrame。

        输出格式：
            columns = [datetime, symbol, factor_1, factor_2, ...]
            按 (symbol, datetime) 排序
        """
        if not factors:
            return pd.DataFrame(columns=["datetime", "symbol"])

        dfs: list[pd.DataFrame] = []
        for f in factors:
            if not f.has_result:
                logger.warning("Skip %s — no result.h5", f.name)
                continue
            try:
                df = self.load_factor(f, convert_codes=convert_codes)
                dfs.append(df)
                logger.info(
                    "Loaded %s: %d rows, %d instruments",
                    f.name, len(df), df["symbol"].nunique(),
                )
            except Exception as e:
                logger.warning("Failed to load %s: %s", f.name, e)

        if not dfs:
            return pd.DataFrame(columns=["datetime", "symbol"])

        # 逐个 merge（outer join on datetime + symbol）
        panel = dfs[0]
        for df in dfs[1:]:
            panel = panel.merge(df, on=["datetime", "symbol"], how="outer")

        panel = panel.sort_values(["symbol", "datetime"]).reset_index(drop=True)
        logger.info(
            "Panel built: %d rows, %d factors, %d instruments",
            len(panel), len(panel.columns) - 2, panel["symbol"].nunique(),
        )
        return panel

    # ------------------------------------------------------------------
    # 报告
    # ------------------------------------------------------------------

    def summary(self, factors: list[DiscoveredFactor]) -> str:
        """生成因子扫描摘要。"""
        lines = [
            f"RD-Agent Factor Summary ({self.base_dir})",
            f"{'Name':<30} {'Rows':>8} {'Inst':>6} {'DateRange':<25} {'OK'}",
            "-" * 80,
        ]
        for f in factors:
            ok = "✓" if f.has_result else "✗"
            dr = f"{f.date_range[0]}~{f.date_range[1]}" if f.date_range[0] else "N/A"
            lines.append(
                f"{f.name:<30} {f.n_rows:>8} {f.n_instruments:>6} {dr:<25} {ok}"
            )
        lines.append(f"\nTotal: {len(factors)} factors, "
                      f"{sum(1 for f in factors if f.has_result)} with results")
        return "\n".join(lines)
