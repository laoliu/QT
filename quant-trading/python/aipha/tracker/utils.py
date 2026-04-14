"""Shared utility functions for the tracker module."""

from __future__ import annotations

import re


def normalize_symbol(raw: str) -> str:
    """Normalize a stock symbol to standard format: '600519.SH', '000001.SZ'.

    Input formats:
        - SH600519 / SZ000001  (Xueqiu format)
        - 600519.SH / 000001.SZ (standard)
        - 600519 / 000001 (bare code)

    Returns:
        Normalized symbol string, e.g. '600519.SH'.
    """
    raw = raw.strip().upper()

    # Already in standard format
    if re.match(r'^\d{6}\.[A-Z]{2}$', raw):
        return raw

    # Xueqiu format: SH600519
    m = re.match(r'^(SH|SZ|BJ)(\d{6})$', raw)
    if m:
        return f"{m.group(2)}.{m.group(1)}"

    # Bare 6-digit code: infer exchange
    m = re.match(r'^(\d{6})$', raw)
    if m:
        code = m.group(1)
        if code.startswith(('6', '9')):
            return f"{code}.SH"
        elif code.startswith(('0', '2', '3')):
            return f"{code}.SZ"
        elif code.startswith(('4', '8')):
            return f"{code}.BJ"
        return f"{code}.SH"  # default fallback

    # If nothing matches return as-is
    return raw
