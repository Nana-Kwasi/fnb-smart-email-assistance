"""Cases: grouping emails about one issue, the timeline, SLA deadlines, reopening and duplicates.

Matching rules (nothing else ever links two emails):
  1. Same Outlook conversation: same case (reopened if it was resolved or closed).
  2. Same Microsoft 365-verified sender address, same category, case still open and recent:
     same case.
  3. Otherwise a new case. If the same address (verified or not) has an open case in the same
     category, the new case is flagged as a possible duplicate, never merged.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import as_utc, utcnow
from app.models import Case, CaseEvent, CaseStatus, CustomerEmail, EmailStatus, ReplyKind, StaffUser

LINK_WINDOW = timedelta(days=30)
SUBSTANTIVE = (ReplyKind.AUTO_ANSWER, ReplyKind.AI_ANSWER, ReplyKind.OFFICER)


def new_reference() -> str:
    return f"CASE-{utcnow():%y%m%d}-{secrets.token_hex(3).upper()}"


def add_event(
    db: Session,
    case: Case,
    kind: str,
    *,
    actor: StaffUser | None = None,
    email: CustomerEmail | None = None,
    text: str | None = None,
    detail: dict[str, Any] | None = None,
) -> CaseEvent:
    event = CaseEvent(
        case_id=case.id,
        kind=kind,
        actor_id=actor.id if actor else None,
        actor_name=(actor.display_name or actor.username) if actor else "Assistant",
        email_id=email.id if email else None,
        text=text,
        detail=detail or {},
        at=utcnow(),
    )
    db.add(event)
    case.last_activity_at = event.at
    return event


def _find_case(db: Session, email: CustomerEmail, category: str | None) -> tuple[Case | None, Case | None]:
    """(case to join, possible duplicate)."""
    if email.conversation_id:
        same_thread = db.scalar(
            select(Case)
            .where(Case.conversation_id == email.conversation_id)
            .order_by(Case.opened_at.desc())
            .limit(1)
        )
        if same_thread is not None:
            return same_thread, None
    if not category:
        return None, None
    since = utcnow() - LINK_WINDOW
    open_same = db.scalar(
        select(Case)
        .where(
            Case.customer_address == email.from_address.lower(),
            Case.category == category,
            Case.status == CaseStatus.OPEN,
            Case.opened_at >= since,
        )
        .order_by(Case.opened_at.desc())
        .limit(1)
    )
    if open_same is None:
        return None, None
    if email.sender_verified and open_same.sender_verified:
        return open_same, None
    return None, open_same


def attach(
    db: Session,
    email: CustomerEmail,
    *,
    category: str | None,
    department: str | None,
    priority: str,
    sla_hours: int | None,
) -> Case:
    case, duplicate = _find_case(db, email, category)
    if case is None:
        now = utcnow()
        case = Case(
            reference=new_reference(),
            customer_address=email.from_address.lower(),
            customer_name=email.from_name,
            sender_verified=email.sender_verified,
            conversation_id=email.conversation_id,
            category=category,
            department=department,
            priority=priority,
            opened_at=now,
            last_activity_at=now,
            sla_due_at=now + timedelta(hours=sla_hours) if sla_hours else None,
            possible_duplicate_of_id=duplicate.id if duplicate else None,
        )
        db.add(case)
        db.flush()
        add_event(
            db,
            case,
            "opened",
            email=email,
            detail={
                "reference": email.reference,
                "possible_duplicate_of": duplicate.reference if duplicate else None,
            },
        )
    else:
        if case.status != CaseStatus.OPEN:
            case.status, case.resolved_at = CaseStatus.OPEN, None
            case.reopened_count = (case.reopened_count or 0) + 1
            if sla_hours:
                case.sla_due_at = utcnow() + timedelta(hours=sla_hours)
            add_event(db, case, "reopened", email=email, detail={"reference": email.reference})
        add_event(db, case, "email_received", email=email, detail={"reference": email.reference})
        if priority == "high" and case.priority != "high":
            case.priority = "high"
        if category and not case.category:
            case.category, case.department = category, department
    email.case_id = case.id
    return case


def sync(db: Session, case: Case) -> None:
    """Resolve the case when no email in it is waiting; note the first real answer."""
    emails = list(db.scalars(select(CustomerEmail).where(CustomerEmail.case_id == case.id)).unique())
    answered = [
        as_utc(r.created_at) for e in emails for r in e.replies if r.succeeded and r.kind in SUBSTANTIVE
    ] + [
        as_utc(e.handled_at)
        for e in emails
        if e.handled_at and e.status in (EmailStatus.REPLIED,) and e.handled_via != "automatic"
    ]
    if answered:
        first = min(answered)
        if case.first_response_at is None or first < as_utc(case.first_response_at):
            case.first_response_at = first
    waiting = any(e.status in EmailStatus.OPEN for e in emails)
    if case.status == CaseStatus.OPEN and emails and not waiting:
        case.status, case.resolved_at = CaseStatus.RESOLVED, utcnow()
        add_event(db, case, "resolved")
