from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Caller, require
from app.core.db import get_db
from app.models import AuditEvent, NotificationLog, StaffUser
from app.schemas import AuditOut, NotificationOut, Page, UserAdminOut, UserUpdateIn
from app.security.rbac import Permission
from app.security.sessions import end_all_sessions
from app.services import audit

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=list[UserAdminOut])
def users(_: Caller = Depends(require(Permission.VIEW_USERS)), db: Session = Depends(get_db)):
    """Everyone who has signed in. Who may sign in at all is decided by the AD group."""
    return db.scalars(select(StaffUser).order_by(StaffUser.last_login_at.desc())).all()


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: uuid.UUID,
    body: UserUpdateIn,
    caller: Caller = Depends(require(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
):
    """Change someone's role or switch their access off. Takes effect immediately."""
    user = db.get(StaffUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if user.id == caller.user.id:
        # Also guarantees there is always at least one admin left.
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot change your own role or access.")
    before = {"role": user.role, "is_active": user.is_active}
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    after = {"role": user.role, "is_active": user.is_active}
    if after != before:
        end_all_sessions(db, user.id)  # the change applies to their very next click
        audit.record(
            db,
            "user.updated",
            actor=caller.user,
            entity_type="user",
            entity_id=user.id,
            detail={"username": user.username, "before": before, "after": after},
            ip=caller.ip,
        )
    db.commit()
    return user


@router.get("/audit", response_model=Page[AuditOut])
def audit_log(
    _: Caller = Depends(require(Permission.VIEW_AUDIT)),
    db: Session = Depends(get_db),
    action: str | None = Query(None, max_length=64),
    actor: str | None = Query(None, max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if actor:
        stmt = stmt.where(AuditEvent.actor_username == actor.lower())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AuditEvent.at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[AuditOut](
        items=[AuditOut.model_validate(r) for r in rows], total=total, page=page, page_size=page_size
    )


@router.get("/notifications", response_model=Page[NotificationOut])
def notification_log(
    _: Caller = Depends(require(Permission.VIEW_AUDIT)),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Every alert and digest sent to the people in charge."""
    total = db.scalar(select(func.count()).select_from(NotificationLog)) or 0
    rows = db.scalars(
        select(NotificationLog)
        .order_by(NotificationLog.sent_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(r) for r in rows], total=total, page=page, page_size=page_size
    )
