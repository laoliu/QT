"""
Stock screener strategies package.

Provides pluggable screener implementations:
  - CAN SLIM (growth + momentum)
  - Piotroski F-Score (value quality)
  - Magic Formula (Greenblatt)
  - Technical (MA/RSI/MACD based)
  - Volume-Price Anomaly
  - PEG / GARP (growth at reasonable price)

Usage:
    from screeners.base import get_all_screeners, get_screener
    screener = get_screener("canslim")
    results = screener.screen(stock_universe)
"""

from .base import BaseScreener, get_all_screeners, get_screener

__all__ = ["BaseScreener", "get_all_screeners", "get_screener"]
