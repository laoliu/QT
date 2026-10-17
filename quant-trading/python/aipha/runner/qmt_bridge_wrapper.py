"""miniQMT data bridge strategy wrapper.

When loaded as a QMT strategy, starts the HTTP data bridge service
(``qmt_data_bridge``) in a background daemon thread so that external
applications can query XtQuant market data via HTTP.

Adapted from QT qmt_bridge_strategy.py for AlphQ architecture.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_bridge_started = False
_bridge_lock = threading.Lock()

DEFAULT_PORT = 18888


def _start_bridge_thread(port: int = DEFAULT_PORT) -> bool:
    """Start qmt_data_bridge HTTP service in a daemon thread.

    Returns True if bridge was started, False if already running
    or if the module is unavailable.
    """
    global _bridge_started
    with _bridge_lock:
        if _bridge_started:
            return False
        try:
            from aipha.execution.qmt_data_bridge import start_bridge
        except ImportError:
            logger.warning(
                "qmt_data_bridge module not available; bridge not started"
            )
            return False

        def _run() -> None:
            try:
                start_bridge(port=port)
            except Exception:
                logger.exception("Data bridge crashed")

        t = threading.Thread(target=_run, name="qmt-data-bridge", daemon=True)
        t.start()
        _bridge_started = True
        logger.info("QMT data bridge started on port %d", port)
        return True


def init(context: Any) -> None:
    """QMT strategy ``init`` callback.

    Launches the HTTP data bridge in a background thread on first call.
    """
    port = getattr(context, "bridge_port", DEFAULT_PORT)
    _start_bridge_thread(port)


def handlebar(context: Any) -> None:
    """QMT strategy ``handlebar`` callback — no-op for data bridge."""
    pass
