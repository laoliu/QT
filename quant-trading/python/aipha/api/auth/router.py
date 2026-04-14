"""Auth router — login / refresh / me / change-password."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from aipha.api.deps import (
    create_access_token,
    create_refresh_token,
    get_result_store,
    _decode_jwt,
    _ACCESS_TOKEN_EXPIRE,
)
from aipha.api.schemas import (
    ApiResponse,
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UserInfo,
)
from aipha.api.auth import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _verify_password(plain: str, hashed: str) -> bool:
    return _hash_password(plain) == hashed


@router.post("/login", response_model=ApiResponse[TokenResponse])
async def login(body: LoginRequest):
    """Authenticate and return JWT tokens."""
    store = get_result_store()
    row = store.get_user(body.username)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    stored_hash = row.get("password_hash", "")
    if not _verify_password(body.password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not row.get("is_active", True):
        raise HTTPException(status_code=403, detail="User deactivated")

    role = row.get("role", "viewer")
    access = create_access_token(body.username, role)
    refresh = create_refresh_token(body.username)

    # Record login timestamp
    try:
        store.record_user_login(body.username, success=True)
    except Exception:
        pass

    return ApiResponse(
        data=TokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=_ACCESS_TOKEN_EXPIRE,
        )
    )


@router.post("/refresh", response_model=ApiResponse[TokenResponse])
async def refresh_token(body: dict):
    """Refresh an access token using a refresh token."""
    refresh = body.get("refresh_token", "")
    if not refresh:
        raise HTTPException(status_code=400, detail="refresh_token required")
    payload = _decode_jwt(refresh)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    username = payload.get("sub", "")
    store = get_result_store()
    row = store.get_user(username)
    if row is None:
        raise HTTPException(status_code=401, detail="User not found")
    role = row.get("role", "viewer")
    access = create_access_token(username, role)
    new_refresh = create_refresh_token(username)
    return ApiResponse(
        data=TokenResponse(
            access_token=access,
            refresh_token=new_refresh,
            expires_in=_ACCESS_TOKEN_EXPIRE,
        )
    )


@router.get("/me", response_model=ApiResponse[UserInfo])
async def get_me(user: UserInfo = Depends(get_current_user)):
    """Get current user profile."""
    return ApiResponse(data=user)


@router.put("/me/password", response_model=ApiResponse)
async def change_password(
    body: ChangePasswordRequest,
    user: UserInfo = Depends(get_current_user),
):
    """Change current user's password."""
    store = get_result_store()
    row = store.get_user(user.username)
    if not _verify_password(body.old_password, row.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Old password incorrect")
    new_hash = _hash_password(body.new_password)
    store.update_user_password(user.username, new_hash)
    return ApiResponse(message="Password updated")
