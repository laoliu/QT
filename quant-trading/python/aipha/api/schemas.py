"""Pydantic response / request models shared across API routers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# ── Unified API response ──────────────────────────────────────
class ApiResponse(BaseModel, Generic[T]):
    """Standard envelope for every API response."""

    code: int = 0
    message: str = "ok"
    data: Optional[T] = None


class PaginationMeta(BaseModel):
    page: int = 1
    page_size: int = 20
    total: int = 0
    total_pages: int = 0


class PaginatedResponse(BaseModel, Generic[T]):
    code: int = 0
    message: str = "ok"
    data: Optional[List[T]] = None
    pagination: Optional[PaginationMeta] = None


# ── Auth ──────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 1800


class UserInfo(BaseModel):
    username: str
    name: Optional[str] = None
    role: str = "viewer"
    email: Optional[str] = None
    is_active: bool = True
    last_login: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
