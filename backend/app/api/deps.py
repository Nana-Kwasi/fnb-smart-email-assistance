"""Request dependencies: who is calling, and are they allowed to.

Every route except login and health requires a valid session. Every request that changes
something must also carry the session's CSRF token in the X-CSRF-Token header, which a page
on another site cannot read or forge.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.models import StaffSession, StaffUser
from app.security.rbac import Permission, has_permission
from app.security.sessions import COOKIE_NAME, CSRF_HEADER, load_session

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass
class Caller:
    user: StaffUser
    session: StaffSession
    ip: str | None


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def current_caller(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> Caller:
    token = request.cookies.get(COOKIE_NAME)
    session = load_session(db, token, settings) if token else None
    if session is None or not session.user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent, session.csrf_token):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Security check failed. Refresh the page and try again."
            )
    return Caller(user=session.user, session=session, ip=client_ip(request))


def require(permission: Permission) -> Callable[..., Caller]:
    def _check(caller: Caller = Depends(current_caller)) -> Caller:
        if not has_permission(caller.user.role, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You do not have permission to do this.")
        return caller

    return _check
