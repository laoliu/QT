"""Dependency injection for FastAPI — DB session, current user, RBAC."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, Header, status

from aipha.backtest.result_store import ResultStore

# ── Default paths ─────────────────────────────────────────────
_OUTPUT_DIR = Path(os.environ.get("AIPHA_OUTPUT_DIR", "output"))
_DB_PATH = _OUTPUT_DIR / "backtest_index.db"


@lru_cache(maxsize=1)
def get_result_store() -> ResultStore:
    """Singleton ResultStore backed by SQLite."""
    return ResultStore(str(_DB_PATH))


# ── JWT helpers (thin wrapper) ────────────────────────────────
_JWT_SECRET = os.environ.get("AIPHA_JWT_SECRET", "aipha-dev-secret-change-me")
_JWT_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE = 1800  # 30 min
_REFRESH_TOKEN_EXPIRE = 604800  # 7 days

ROLE_LEVELS = {
    "viewer": 10,
    "researcher": 20,
    "ops": 30,
    "risk_approver": 40,
    "admin": 99,
}


def _decode_jwt(token: str) -> dict:
    """Decode and validate a JWT token, return payload."""
    import jwt as pyjwt

    try:
        payload = pyjwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def create_access_token(username: str, role: str) -> str:
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=_ACCESS_TOKEN_EXPIRE),
        "type": "access",
    }
    return pyjwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def create_refresh_token(username: str) -> str:
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=_REFRESH_TOKEN_EXPIRE),
        "type": "refresh",
    }
    return pyjwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)
