"""Dependency — current user extraction & role guards."""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from aipha.api.deps import _decode_jwt, ROLE_LEVELS, get_result_store
from aipha.api.schemas import UserInfo

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserInfo:
    """Extract and validate the current user from JWT Bearer token."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = _decode_jwt(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    store = get_result_store()
    row = store.get_user(username)
    if row is None:
        raise HTTPException(status_code=401, detail="User not found")
    if not row.get("is_active", True):
        raise HTTPException(status_code=403, detail="User deactivated")
    return UserInfo(
        username=row["username"],
        name=row.get("name", ""),
        role=row.get("role", "viewer"),
        email=row.get("email", ""),
        is_active=row.get("is_active", True),
        last_login=row.get("last_login"),
    )


def require_role(min_role: str):
    """Factory that returns a dependency enforcing min role level."""

    async def _check(user: UserInfo = Depends(get_current_user)) -> UserInfo:
        user_level = ROLE_LEVELS.get(user.role, 0)
        required_level = ROLE_LEVELS.get(min_role, 0)
        if user_level < required_level:
            raise HTTPException(
                status_code=403,
                detail=f"Requires role '{min_role}' or above",
            )
        return user

    return _check
