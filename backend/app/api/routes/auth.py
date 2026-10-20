from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Caller, client_ip, current_caller
from app.core.config import Settings, get_settings
from app.core.db import get_db, utcnow
from app.models import StaffUser
from app.schemas import FeaturesOut, LoginIn, MeOut, MeUserOut
from app.security import dev_auth, ldap_auth
from app.security.rbac import Role, permissions_for
from app.security.sessions import COOKIE_NAME, create_session, end_session, is_locked_out, record_attempt
from app.services import audit
from app.services.app_settings import get_app_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

LOCKED_OUT = "Too many failed attempts. Try again in a few minutes."
ACCESS_DISABLED = "Your access to Email Care has been switched off. Contact an administrator."


def _me(user: StaffUser, csrf_token: str, db: Session) -> MeOut:
    settings = get_settings()
    cfg = get_app_settings(db)
    return MeOut(
        user=MeUserOut.model_validate(user),
        permissions=sorted(permissions_for(user.role)),
        csrf_token=csrf_token,
        features=FeaturesOut(
            customer_data_answers=settings.customer_data_answers,
            max_data_sources=None if settings.customer_data_answers else settings.phase1_max_data_sources,
            require_second_person=cfg.require_second_person,
            max_upload_mb=cfg.max_upload_mb,
        ),
    )


@router.get("/mode")
def sign_in_mode(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    """Lets the login page warn when development sign-in is on."""
    return {"mode": settings.auth_mode}


@router.post("/login", response_model=MeOut)
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MeOut:
    username = body.username.lower()
    ip = client_ip(request)
    if is_locked_out(db, username, settings):
        audit.record(db, "auth.locked_out", detail={"username": username}, ip=ip)
        db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, LOCKED_OUT)

    try:
        if settings.auth_mode == "dev":
            directory_user = dev_auth.authenticate(username, body.password)
        else:
            directory_user = ldap_auth.authenticate(username, body.password, settings)
    except ldap_auth.DirectoryUnavailable:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Sign-in is temporarily unavailable. Please try again shortly.",
        ) from None
    except ldap_auth.AuthError as exc:
        record_attempt(db, username, ip, success=False)
        audit.record(db, "auth.failed", detail={"username": username, "reason": str(exc)}, ip=ip)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from None

    user = db.scalar(select(StaffUser).where(StaffUser.username == directory_user.username))
    if user is None:
        # First sign-in: everyone starts as an officer, except the bootstrap admins from .env.
        first_role = (
            Role.ADMIN if directory_user.username in settings.bootstrap_admin_usernames else Role.OFFICER
        )
        user = StaffUser(username=directory_user.username, role=first_role.value)
        db.add(user)
        audit.record(db, "user.created", detail={"username": user.username, "role": user.role}, ip=ip)
    elif not user.is_active:
        record_attempt(db, username, ip, success=False)
        audit.record(db, "auth.refused_disabled", detail={"username": username}, ip=ip)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, ACCESS_DISABLED)
    # Name and email follow AD; the role is managed in the dashboard and is not touched here.
    user.display_name = directory_user.display_name
    user.email = directory_user.email
    user.previous_login_at, user.last_login_at = user.last_login_at, utcnow()
    db.flush()

    record_attempt(db, username, ip, success=True)
    token, session = create_session(db, user, settings, ip)
    audit.record(db, "auth.login", actor=user, detail={"role": user.role}, ip=ip)
    db.commit()

    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_max_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api",
    )
    return _me(user, session.csrf_token, db)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def logout(
    request: Request,
    response: Response,
    caller: Caller = Depends(current_caller),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    audit.record(db, "auth.logout", actor=caller.user, ip=caller.ip)
    db.commit()
    end_session(db, request.cookies[COOKIE_NAME], settings)
    response.delete_cookie(COOKIE_NAME, path="/api")


@router.get("/me", response_model=MeOut)
def me(caller: Caller = Depends(current_caller), db: Session = Depends(get_db)) -> MeOut:
    return _me(caller.user, caller.session.csrf_token, db)
