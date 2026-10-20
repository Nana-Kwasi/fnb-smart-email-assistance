"""Cases: every email about one customer issue, its timeline, internal notes and actions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Caller, require
from app.core.db import get_db, utcnow
from app.models import DEPARTMENTS, Case, CaseAction, CaseEvent, CaseStatus, CustomerEmail, StaffUser
from app.schemas import EmailSummaryOut, Page, UserOut
from app.security.rbac import Permission
from app.services import actions, audit, cases
from app.services.app_settings import get_app_settings
from app.services.policy import get_policy

router = APIRouter(prefix="/cases", tags=["cases"])


class CaseSummaryOut(BaseModel):
    id: uuid.UUID
    reference: str
    customer_address: str
    customer_name: str
    sender_verified: bool
    status: str
    category: str | None
    category_label: str | None
    department: str | None
    priority: str
    assigned_to: UserOut | None
    opened_at: datetime
    sla_due_at: datetime | None
    sla_breached: bool
    first_response_at: datetime | None
    resolved_at: datetime | None
    last_activity_at: datetime
    reopened_count: int
    emails: int
    possible_duplicate_of: str | None


class EventOut(BaseModel):
    id: uuid.UUID
    at: datetime
    kind: str
    actor_name: str
    email_id: uuid.UUID | None
    text: str | None
    detail: dict


class ActionOut(BaseModel):
    id: uuid.UUID
    action: str
    label: str
    params: dict
    status: str
    result: str | None
    requested_by: UserOut
    decided_by: UserOut | None
    created_at: datetime
    decided_at: datetime | None


class CaseDetailOut(CaseSummaryOut):
    email_list: list[EmailSummaryOut]
    events: list[EventOut]
    actions: list[ActionOut]
    departments: list[str]


class NoteIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class CaseAssignIn(BaseModel):
    user_id: uuid.UUID | None


class ActionIn(BaseModel):
    action: Annotated[str, StringConstraints(max_length=40)]
    params: dict[str, Any] = {}


class DecideIn(BaseModel):
    approve: bool


def _labels(db: Session) -> dict[str, str]:
    return {c["code"]: c["label"] for c in get_policy(db).categories or []}


def _summary(db: Session, case: Case, labels: dict[str, str], counts: dict | None = None) -> CaseSummaryOut:
    dup = db.get(Case, case.possible_duplicate_of_id) if case.possible_duplicate_of_id else None
    from app.core.db import as_utc

    breached = bool(
        case.sla_due_at
        and as_utc(case.sla_due_at) < (as_utc(case.first_response_at) if case.first_response_at else utcnow())
    )
    return CaseSummaryOut(
        id=case.id,
        reference=case.reference,
        customer_address=case.customer_address,
        customer_name=case.customer_name,
        sender_verified=case.sender_verified,
        status=case.status,
        category=case.category,
        category_label=labels.get(case.category or ""),
        department=case.department,
        priority=case.priority,
        assigned_to=UserOut.model_validate(case.assigned_to) if case.assigned_to else None,
        opened_at=case.opened_at,
        sla_due_at=case.sla_due_at,
        sla_breached=breached,
        first_response_at=case.first_response_at,
        resolved_at=case.resolved_at,
        last_activity_at=case.last_activity_at,
        reopened_count=case.reopened_count,
        emails=(counts or {}).get(case.id)
        if counts is not None
        else db.scalar(select(func.count()).where(CustomerEmail.case_id == case.id)) or 0,
        possible_duplicate_of=dup.reference if dup else None,
    )


def _get(db: Session, case_id: uuid.UUID) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found.")
    return case


def _detail(db: Session, case: Case) -> CaseDetailOut:
    db.refresh(case)
    emails = db.scalars(
        select(CustomerEmail).where(CustomerEmail.case_id == case.id).order_by(CustomerEmail.received_at)
    ).unique()
    events = db.scalars(select(CaseEvent).where(CaseEvent.case_id == case.id).order_by(CaseEvent.at))
    rows = db.scalars(select(CaseAction).where(CaseAction.case_id == case.id).order_by(CaseAction.created_at))
    return CaseDetailOut(
        **_summary(db, case, _labels(db)).model_dump(),
        email_list=[EmailSummaryOut.model_validate(e) for e in emails],
        events=[EventOut.model_validate(e, from_attributes=True) for e in events],
        actions=[
            ActionOut(
                id=a.id,
                action=a.action,
                label=actions.ACTIONS[a.action].label if a.action in actions.ACTIONS else a.action,
                params=a.params,
                status=a.status,
                result=a.result,
                requested_by=UserOut.model_validate(a.requested_by),
                decided_by=UserOut.model_validate(a.decided_by) if a.decided_by else None,
                created_at=a.created_at,
                decided_at=a.decided_at,
            )
            for a in rows
        ],
        departments=DEPARTMENTS,
    )


@router.get("", response_model=Page[CaseSummaryOut])
def list_cases(
    caller: Caller = Depends(require(Permission.VIEW_CASES)),
    db: Session = Depends(get_db),
    q: str | None = Query(None, max_length=200),
    status_: Literal["open", "resolved", "closed"] | None = Query(None, alias="status"),
    department: str | None = Query(None, max_length=60),
    category: str | None = Query(None, max_length=32),
    filter_: Literal["mine", "sla", "reopened", "duplicates", "urgent"] | None = Query(None, alias="filter"),
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),
):
    stmt = select(Case)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Case.reference.ilike(like), Case.customer_address.ilike(like), Case.customer_name.ilike(like))
        )
    if status_:
        stmt = stmt.where(Case.status == status_)
    if department:
        stmt = stmt.where(Case.department == department)
    if category:
        stmt = stmt.where(Case.category == category)
    if filter_ == "mine":
        stmt = stmt.where(Case.assigned_to_id == caller.user.id)
    elif filter_ == "sla":
        stmt = stmt.where(
            Case.status == CaseStatus.OPEN, Case.sla_due_at < utcnow(), Case.first_response_at.is_(None)
        )
    elif filter_ == "reopened":
        stmt = stmt.where(Case.reopened_count > 0)
    elif filter_ == "duplicates":
        stmt = stmt.where(Case.possible_duplicate_of_id.is_not(None))
    elif filter_ == "urgent":
        stmt = stmt.where(Case.priority == "high")
    page_size = page_size or get_app_settings(db).page_size
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(
            stmt.order_by(Case.last_activity_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).unique()
    )
    counts = dict(
        db.execute(
            select(CustomerEmail.case_id, func.count())
            .where(CustomerEmail.case_id.in_([c.id for c in rows]))
            .group_by(CustomerEmail.case_id)
        ).all()
    )
    labels = _labels(db)
    return Page[CaseSummaryOut](
        items=[_summary(db, c, labels, counts) for c in rows], total=total, page=page, page_size=page_size
    )


@router.get("/actions/catalog")
def action_catalog(_: Caller = Depends(require(Permission.VIEW_CASES))):
    return actions.catalog()


@router.get("/{case_id}", response_model=CaseDetailOut)
def get_case(
    case_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.VIEW_CASES)),
    db: Session = Depends(get_db),
):
    case = _get(db, case_id)
    audit.record(db, "case.viewed", actor=caller.user, entity_type="case", entity_id=case.id, ip=caller.ip)
    db.commit()
    return _detail(db, case)


@router.post("/{case_id}/notes", response_model=CaseDetailOut)
def add_note(
    case_id: uuid.UUID,
    body: NoteIn,
    caller: Caller = Depends(require(Permission.HANDLE_CASES)),
    db: Session = Depends(get_db),
):
    """An internal note: staff only, never sent to the customer."""
    case = _get(db, case_id)
    cases.add_event(db, case, "note", actor=caller.user, text=body.text)
    audit.record(
        db, "case.note_added", actor=caller.user, entity_type="case", entity_id=case.id, ip=caller.ip
    )
    db.commit()
    return _detail(db, case)


@router.post("/{case_id}/assign", response_model=CaseDetailOut)
def assign_case(
    case_id: uuid.UUID,
    body: CaseAssignIn,
    caller: Caller = Depends(require(Permission.HANDLE_CASES)),
    db: Session = Depends(get_db),
):
    case = _get(db, case_id)
    target = None
    if body.user_id:
        target = db.get(StaffUser, body.user_id)
        if target is None or not target.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That user cannot be assigned cases.")
    case.assigned_to_id = body.user_id
    case.handoffs = (case.handoffs or 0) + 1
    for email in db.scalars(select(CustomerEmail).where(CustomerEmail.case_id == case.id)).unique():
        email.assigned_to_id = body.user_id
    cases.add_event(
        db,
        case,
        "assigned",
        actor=caller.user,
        detail={"to": (target.display_name or target.username) if target else None},
    )
    audit.record(
        db,
        "case.assigned",
        actor=caller.user,
        entity_type="case",
        entity_id=case.id,
        detail={"to": str(body.user_id) if body.user_id else None},
        ip=caller.ip,
    )
    db.commit()
    return _detail(db, case)


@router.post("/{case_id}/actions", response_model=CaseDetailOut)
def run_action(
    case_id: uuid.UUID,
    body: ActionIn,
    caller: Caller = Depends(require(Permission.HANDLE_CASES)),
    db: Session = Depends(get_db),
):
    case = _get(db, case_id)
    try:
        actions.request(db, case, body.action, body.params, caller.user, ip=caller.ip)
    except actions.ActionError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    db.commit()
    return _detail(db, case)


@router.post("/actions/{action_id}/decide", response_model=CaseDetailOut)
def decide_action(
    action_id: uuid.UUID,
    body: DecideIn,
    caller: Caller = Depends(require(Permission.APPROVE_ACTIONS)),
    db: Session = Depends(get_db),
):
    row = db.get(CaseAction, action_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Action not found.")
    try:
        actions.decide(db, row, body.approve, caller.user, ip=caller.ip)
    except actions.ActionError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return _detail(db, _get(db, row.case_id))
