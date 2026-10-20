"""Server-side sessions held in the database.

The browser only holds a random token in an HttpOnly cookie that JavaScript cannot read.
Sessions expire after a period of inactivity and after a fixed maximum, and can be revoked
instantly by deleting the row (logout does exactly that).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import as_utc, utcnow
from app.models import LoginAttempt, StaffSession, StaffUser
from app.services.app_settings import get_app_settings

COOKIE_NAME = "fnb_care_session"
CSRF_HEADER = "X-CSRF-Token"


def hash_token(token: str, settings: Settings) -> str:
    return hmac.new(settings.session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def create_session(
    db: Session, user: StaffUser, settings: Settings, ip: str | None
) -> tuple[str, StaffSession]:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    row = StaffSession(
        token_hash=hash_token(token, settings),
        csrf_token=secrets.token_urlsafe(32),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_max_hours),
        ip_address=ip,
    )
    db.add(row)
    # Housekeeping: drop this user's expired sessions.
    db.execute(delete(StaffSession).where(StaffSession.user_id == user.id, StaffSession.expires_at < now))
    return token, row


def load_session(db: Session, token: str, settings: Settings) -> StaffSession | None:
    row = db.scalar(select(StaffSession).where(StaffSession.token_hash == hash_token(token, settings)))
    if row is None:
        return None
    now = utcnow()
    idle_limit = as_utc(row.last_seen_at) + timedelta(minutes=get_app_settings(db).session_idle_minutes)
    if now > as_utc(row.expires_at) or now > idle_limit:
        db.delete(row)
        db.commit()
        return None
    row.last_seen_at = now
    db.commit()
    return row


def end_session(db: Session, token: str, settings: Settings) -> None:
    db.execute(delete(StaffSession).where(StaffSession.token_hash == hash_token(token, settings)))
    db.commit()


def end_all_sessions(db: Session, user_id) -> None:
    """Sign a user out everywhere, e.g. after their role changes or access is switched off."""
    db.execute(delete(StaffSession).where(StaffSession.user_id == user_id))


def is_locked_out(db: Session, username: str, settings: Settings) -> bool:
    since = utcnow() - timedelta(minutes=settings.login_lockout_minutes)
    last_success = db.scalar(
        select(func.max(LoginAttempt.attempted_at)).where(
            LoginAttempt.username == username,
            LoginAttempt.success.is_(True),
            LoginAttempt.attempted_at >= since,
        )
    )
    if last_success is not None:
        since = as_utc(last_success)
    failures = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.username == username,
            LoginAttempt.success.is_(False),
            LoginAttempt.attempted_at > since,
        )
    )
    return (failures or 0) >= settings.login_max_failures


def record_attempt(db: Session, username: str, ip: str | None, success: bool) -> None:
    db.add(LoginAttempt(username=username, ip_address=ip, success=success))
