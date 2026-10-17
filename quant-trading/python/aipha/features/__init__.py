"""Canonical feature namespace for the A-share research pipeline.

The first implementation lives in :mod:`aipha.research.snapshots` because
feature materialization must happen against a frozen PIT snapshot.  This
namespace is the stable import boundary for new callers.
"""

from .cn_equity import build_cn_equity_features

__all__ = ["build_cn_equity_features"]
