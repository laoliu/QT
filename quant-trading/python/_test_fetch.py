#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engines.vnpy_engine import _fetch_data

df = _fetch_data('000300', '2025-04-08', '2026-04-08')
if df is None:
    print('DATA: None')
elif hasattr(df, 'empty'):
    print(f'DATA: DataFrame, empty={df.empty}, rows={len(df)}')
    if not df.empty:
        print(df.head(3))
elif isinstance(df, list):
    print(f'DATA: list, len={len(df)}')
else:
    print(f'DATA: type={type(df)}')
