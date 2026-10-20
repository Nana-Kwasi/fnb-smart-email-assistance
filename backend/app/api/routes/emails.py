"""Customer emails: the "Needs attention" list, the full log, and actions on one email.

People can reply in Outlook, as before (the worker notices and marks the email handled), or
from here: check the assistant's draft and its sources, edit it, and send it. Categories that
need a supervisor (complaints, fraud, ...) are submitted by an officer and approved and sent by
a different person with the approve permission. Every send carries a request id chosen by the
browser, so a double click or a retried request never sends twice.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Caller, require
from app.api.services import get_mailbox, get_sources
from app.core.config import Settings, get_settings
from app.core.db import as_utc, get_db, utcnow
from app.models import (
    AiDraft,
    Case,
    CustomerEmail,
    DraftStatus,
    EmailReply,
    EmailStatus,
    HandledVia,
    Priority,
    Reason,
    ReplyKind,
    StaffUser,
    Topic,
)
from app.schemas import (
    AssignIn,
    AttentionOut,
    CaseBriefOut,
    DraftOut,
    EmailDetailOut,
    EmailSummaryOut,
    KnowledgeUsedOut,
    Page,
    ReasonCount,
    RejectIn,
    ResolveIn,
    SendIn,
    SubmitIn,
    SuggestedTopicOut,
    SuggestionOut,
    UserOut,
)
from app.security.rbac import Permission, has_permission
from app.services import audit, cases, safety
from app.services.answer_builder import build_answer
from app.services.app_settings import get_app_settings
from app.services.datasources import DataSources
from app.services.policy import category_policy, get_policy
from app.services.processor import Mailbox, send_reply
from app.services.reports import Column, Report, to_pdf, to_xlsx

router = APIRouter(prefix="/emails", tags=["emails"])

EXPORT_LIMIT = 10_000


def _get(db: Session, email_id: uuid.UUID, *, lock: bool = False) -> CustomerEmail:
    stmt = select(CustomerEmail).where(CustomerEmail.id == email_id)
    if lock:
        stmt = stmt.with_for_update(of=CustomerEmail)
    email = db.scalar(stmt)
    if email is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Email not found.")
    return email


def _detail(db: Session, email: CustomerEmail) -> EmailDetailOut:
    db.refresh(email)
    out = EmailDetailOut.model_validate(email)
    entry = email.knowledge_entry
    if entry is not None:
        out.knowledge = KnowledgeUsedOut(
            title=entry.title,
            content=entry.content,
            document_id=entry.version.document_id,
            document=entry.version.document.name,
            category=entry.version.document.category,
            version=entry.version.version_label,
        )
    if email.match_topic_id:
        topic = db.get(Topic, email.match_topic_id)
        if topic and topic.is_active and topic.live_revision:
            out.suggested = SuggestedTopicOut(topic_id=topic.id, title=topic.live_revision.title)
    out.drafts = [
        DraftOut.model_validate(d)
        for d in db.scalars(
            select(AiDraft).where(AiDraft.email_id == email.id).order_by(AiDraft.created_at.desc())
        )
    ]
    if email.case_id:
        case = db.get(Case, email.case_id)
        if case is not None:
            dup = db.get(Case, case.possible_duplicate_of_id) if case.possible_duplicate_of_id else None
            out.case = CaseBriefOut(
                id=case.id,
                reference=case.reference,
                status=case.status,
                emails=db.scalar(select(func.count()).where(CustomerEmail.case_id == case.id)) or 0,
                reopened_count=case.reopened_count,
                sla_due_at=case.sla_due_at,
                possible_duplicate_of=dup.reference if dup else None,
            )
    out.approval = _approval(db, email)
    return out


def _approval(db: Session, email: CustomerEmail) -> str:
    cat = category_policy(get_policy(db), email.category)
    return (
        "supervisor"
        if (cat and cat.get("approval") == "supervisor") or email.reason_code == Reason.URGENT
        else "officer"
    )


def _open_draft(db: Session, email: CustomerEmail, draft_id: uuid.UUID | None) -> AiDraft | None:
    if draft_id is None:
        return None
    draft = db.get(AiDraft, draft_id)
    if draft is None or draft.email_id != email.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Draft not found.")
    if draft.status not in DraftStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, "This draft is no longer open.")
    return draft


def _check_outgoing(text: str) -> None:
    """An officer's reply must never ask the customer for secrets."""
    problems = [p for p in safety.unsafe_answer_content(text, text) if "PIN" in p]
    if problems:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The reply " + problems[0] + ". The bank never asks for these by email.",
        )


def _page(db: Session, stmt, page: int, page_size: int | None) -> Page[EmailSummaryOut]:
    page_size = page_size or get_app_settings(db).page_size
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).unique()
    return Page[EmailSummaryOut](
        items=[EmailSummaryOut.model_validate(r) for r in rows],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


def _log_query(q: str | None, status_: str | None, date_from: date | None, date_to: date | None):
    stmt = select(CustomerEmail)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                CustomerEmail.reference.ilike(like),
                CustomerEmail.from_address.ilike(like),
                CustomerEmail.from_name.ilike(like),
                CustomerEmail.subject.ilike(like),
            )
        )
    if status_:
        stmt = stmt.where(CustomerEmail.status == status_)
    if date_from:
        stmt = stmt.where(CustomerEmail.received_at >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(CustomerEmail.received_at < datetime.combine(date_to + timedelta(days=1), time.min))
    return stmt.order_by(CustomerEmail.received_at.desc())


@router.get("/attention", response_model=AttentionOut)
def attention(
    caller: Caller = Depends(require(Permission.VIEW_QUEUE)),
    db: Session = Depends(get_db),
    reason: str | None = Query(None, max_length=32),
    priority: str | None = Query(None, pattern="^(normal|high)$"),
    mine: bool = False,
    overdue: bool = False,
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),  # default: Settings
):
    """Emails a person still needs to answer, newest first."""
    cfg = get_app_settings(db)
    cutoff = utcnow() - timedelta(hours=cfg.overdue_after_hours)
    waiting = CustomerEmail.status == EmailStatus.QUEUED

    counts = db.execute(
        select(CustomerEmail.reason_code, func.count()).where(waiting).group_by(CustomerEmail.reason_code)
    ).all()
    reasons = sorted(
        (
            ReasonCount(
                code=code or Reason.NO_MATCH, label=Reason.LABELS.get(code or Reason.NO_MATCH, code), count=n
            )
            for code, n in counts
        ),
        key=lambda r: -r.count,
    )
    overdue_count = db.scalar(select(func.count()).where(waiting, CustomerEmail.received_at <= cutoff)) or 0

    filter_ = "urgent" if priority == "high" else "overdue" if overdue else "mine" if mine else None
    stmt = _attention_query(cfg, caller.user.id, reason, filter_)
    db.commit()
    return AttentionOut(
        page=_page(db, stmt, page, page_size),
        reasons=reasons,
        overdue=overdue_count,
        overdue_after_hours=cfg.overdue_after_hours,
    )


@router.get("", response_model=Page[EmailSummaryOut])
def email_log(
    _: Caller = Depends(require(Permission.VIEW_EMAIL_LOG)),
    db: Session = Depends(get_db),
    q: str | None = Query(None, max_length=200),
    status_: str | None = Query(None, alias="status", max_length=20),
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),  # default: Settings
):
    return _page(db, _log_query(q, status_, date_from, date_to), page, page_size)


def _attention_query(cfg, user_id, reason: str | None, filter_: str | None):
    cutoff = utcnow() - timedelta(hours=cfg.overdue_after_hours)
    stmt = select(CustomerEmail).where(CustomerEmail.status == EmailStatus.QUEUED)
    if reason:
        stmt = stmt.where(CustomerEmail.reason_code == reason)
    if filter_ == "urgent":
        stmt = stmt.where(CustomerEmail.priority == Priority.HIGH)
    elif filter_ == "overdue":
        stmt = stmt.where(CustomerEmail.received_at <= cutoff)
    elif filter_ == "mine":
        stmt = stmt.where(CustomerEmail.assigned_to_id == user_id)
    return stmt.order_by(CustomerEmail.received_at.desc())


def _waiting(received: datetime) -> str:
    minutes = max(0, int((utcnow() - as_utc(received)).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    return f"{hours} h {minutes % 60} min" if hours < 24 else f"{hours // 24} d {hours % 24} h"


STATUS_WORDS = {
    EmailStatus.RECEIVED: "Processing",
    EmailStatus.QUEUED: "Needs a person",
    EmailStatus.AUTO_ANSWERED: "Answered automatically",
    EmailStatus.REPLIED: "Answered by a person",
    EmailStatus.CLOSED: "Closed",
    EmailStatus.SKIPPED: "Not a customer email",
}


@router.get("/export.{fmt}")
def export(
    fmt: Literal["pdf", "xlsx"],
    caller: Caller = Depends(require(Permission.EXPORT_EMAIL_LOG)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    scope: Literal["log", "attention"] = "log",
    q: str | None = Query(None, max_length=200),
    status_: str | None = Query(None, alias="status", max_length=20),
    date_from: date | None = None,
    date_to: date | None = None,
    reason: str | None = Query(None, max_length=32),
    filter_: str | None = Query(None, alias="filter", pattern="^(urgent|overdue|mine)$"),
):
    """The list on screen (same filters), as a PDF or Excel file with the FNB layout."""
    cfg = get_app_settings(db)
    zone = ZoneInfo(settings.timezone)
    if scope == "attention":
        stmt = _attention_query(cfg, caller.user.id, reason, filter_)
        parts = [
            Reason.LABELS.get(reason, reason) if reason else "Any reason",
            {
                "urgent": "urgent only",
                "overdue": f"over {cfg.overdue_after_hours} h",
                "mine": "assigned to me",
            }.get(filter_ or "", "all waiting"),
        ]
        title = "Needs attention"
        columns = [
            Column("Reference", 16),
            Column("Received", 14),
            Column("Waiting", 10),
            Column("Customer", 24),
            Column("Subject", 26),
            Column("Why a person is needed", 34),
            Column("Owner", 14),
        ]
    else:
        stmt = _log_query(q, status_, date_from, date_to)
        parts = [
            f"Search “{q}”" if q else "All emails",
            STATUS_WORDS.get(status_, "any status") if status_ else "any status",
        ]
        if date_from or date_to:
            parts.append(f"{date_from or '…'} to {date_to or '…'}")
        title = "Email log"
        columns = [
            Column("Reference", 16),
            Column("Received", 14),
            Column("Customer", 24),
            Column("Subject", 26),
            Column("Status", 16),
            Column("Details", 34),
            Column("Handled", 14),
        ]
    emails = db.scalars(stmt.limit(EXPORT_LIMIT)).unique().all()

    def when(dt):
        return as_utc(dt).astimezone(zone).strftime("%d %b %Y %H:%M") if dt else ""

    rows = []
    for e in emails:
        customer = f"{e.from_name}\n{e.from_address}" if e.from_name else e.from_address
        reason_text = (("URGENT · " if e.priority == Priority.HIGH else "") + (e.status_reason or "")).strip()
        if scope == "attention":
            rows.append(
                [
                    e.reference,
                    when(e.received_at),
                    _waiting(e.received_at),
                    customer,
                    e.subject or "(no subject)",
                    reason_text,
                    e.assigned_to.display_name if e.assigned_to else "Nobody yet",
                ]
            )
        else:
            handled = (
                e.handled_by.display_name
                if e.handled_by
                else {"automatic": "Automatically", "outlook": "In Outlook"}.get(e.handled_via or "", "")
            ) + (f"\n{when(e.handled_at)}" if e.handled_at else "")
            rows.append(
                [
                    e.reference,
                    when(e.received_at),
                    customer,
                    e.subject or "(no subject)",
                    STATUS_WORDS.get(e.status, e.status),
                    reason_text,
                    handled.strip(),
                ]
            )

    report = Report(
        title=title,
        subtitle=" · ".join(parts)
        + f" · {len(rows)} email{'s' if len(rows) != 1 else ''}"
        + (f" (first {EXPORT_LIMIT})" if len(rows) == EXPORT_LIMIT else ""),
        generated_by=caller.user.display_name or caller.user.username,
        generated_at=datetime.now(zone),
        columns=columns,
        rows=rows,
    )
    audit.record(
        db,
        "emails.exported",
        actor=caller.user,
        detail={"format": fmt, "scope": scope, "rows": len(rows)},
        ip=caller.ip,
    )
    db.commit()
    stamp = datetime.now(zone).strftime("%Y%m%d-%H%M")
    name = f"{'needs-attention' if scope == 'attention' else 'email-log'}-{stamp}.{fmt}"
    if fmt == "pdf":
        return Response(
            to_pdf(report),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    return Response(
        to_xlsx(report),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/assignable-users", response_model=list[UserOut])
def assignable_users(_: Caller = Depends(require(Permission.HANDLE_QUEUE)), db: Session = Depends(get_db)):
    return db.scalars(
        select(StaffUser).where(StaffUser.is_active.is_(True)).order_by(StaffUser.display_name)
    ).all()


@router.get("/{email_id}", response_model=EmailDetailOut)
def get_email(
    email_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.VIEW_EMAIL_LOG)),
    db: Session = Depends(get_db),
):
    email = _get(db, email_id)
    audit.record(db, "email.viewed", actor=caller.user, entity_type="email", entity_id=email.id, ip=caller.ip)
    db.commit()
    return _detail(db, email)


@router.get("/{email_id}/suggestion/{topic_id}", response_model=SuggestionOut)
def suggestion(
    email_id: uuid.UUID,
    topic_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.HANDLE_QUEUE)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    """The answer a topic would give, built from the bank's data, to copy into an Outlook reply."""
    email = _get(db, email_id)
    topic = db.scalar(select(Topic).options(selectinload(Topic.live_revision)).where(Topic.id == topic_id))
    if topic is None or not topic.is_active or topic.live_revision is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That topic is not live.")
    built = build_answer(db, topic.live_revision, email, get_app_settings(db), sources)
    audit.record(
        db,
        "email.suggestion_viewed",
        actor=caller.user,
        entity_type="email",
        entity_id=email.id,
        detail={"topic_id": str(topic.id), "found": built.text is not None},
        ip=caller.ip,
    )
    db.commit()
    return SuggestionOut(text=built.text, reason=built.reason)


@router.post("/{email_id}/assign", response_model=EmailDetailOut)
def assign(
    email_id: uuid.UUID,
    body: AssignIn,
    caller: Caller = Depends(require(Permission.HANDLE_QUEUE)),
    db: Session = Depends(get_db),
):
    email = _get(db, email_id, lock=True)
    target = None
    if body.user_id:
        target = db.get(StaffUser, body.user_id)
        if target is None or not target.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That user cannot be assigned emails.")
    email.assigned_to_id = body.user_id
    if email.case_id and (case := db.get(Case, email.case_id)):
        case.assigned_to_id = body.user_id
        case.handoffs = (case.handoffs or 0) + 1
        cases.add_event(
            db,
            case,
            "assigned",
            actor=caller.user,
            email=email,
            detail={"to": target.display_name if body.user_id else None},
        )
    audit.record(
        db,
        "email.assigned",
        actor=caller.user,
        entity_type="email",
        entity_id=email.id,
        detail={"to": str(body.user_id) if body.user_id else None},
        ip=caller.ip,
    )
    db.commit()
    return _detail(db, email)


@router.post("/{email_id}/resolve", response_model=EmailDetailOut)
def resolve(
    email_id: uuid.UUID,
    body: ResolveIn,
    caller: Caller = Depends(require(Permission.HANDLE_QUEUE)),
    db: Session = Depends(get_db),
):
    """Mark handled: answered some other way (phone, branch, another mailbox), or no reply needed."""
    email = _get(db, email_id, lock=True)
    if email.status not in EmailStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is already handled.")
    if body.outcome == "closed" and len(body.note) < 3:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Say briefly why no reply is needed.")
    email.status = EmailStatus.REPLIED if body.outcome == "replied" else EmailStatus.CLOSED
    email.status_reason = "Marked as answered by a person" if body.outcome == "replied" else "No reply needed"
    email.handled_by_id, email.handled_at, email.handled_via = caller.user.id, utcnow(), HandledVia.DASHBOARD
    email.handled_note = body.note or None
    for draft in db.scalars(
        select(AiDraft).where(AiDraft.email_id == email.id, AiDraft.status.in_(DraftStatus.OPEN))
    ):
        draft.status = DraftStatus.SUPERSEDED
    if email.case_id and (case := db.get(Case, email.case_id)):
        cases.add_event(
            db, case, f"email_{body.outcome}", actor=caller.user, email=email, detail={"note": body.note}
        )
        cases.sync(db, case)
    audit.record(
        db,
        f"email.{body.outcome}",
        actor=caller.user,
        entity_type="email",
        entity_id=email.id,
        detail={"reference": email.reference, "note": body.note},
        ip=caller.ip,
    )
    db.commit()
    return _detail(db, email)


@router.post("/{email_id}/submit", response_model=EmailDetailOut)
def submit_for_approval(
    email_id: uuid.UUID,
    body: SubmitIn,
    caller: Caller = Depends(require(Permission.SEND_REPLIES)),
    db: Session = Depends(get_db),
):
    """Officer: put a reply (the assistant's draft, edited or not, or their own) up for approval."""
    email = _get(db, email_id, lock=True)
    if email.status != EmailStatus.QUEUED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is not waiting for a reply.")
    _check_outgoing(body.text)
    draft = _open_draft(db, email, body.draft_id)
    if draft is None:
        draft = AiDraft(
            email_id=email.id,
            body_text=body.text,
            model="written by staff",
            passed=False,
            policy_version=get_policy(db).version,
        )
        db.add(draft)
    draft.edited_text = body.text if body.text.strip() != draft.body_text.strip() else None
    draft.status, draft.submitted_by_id, draft.submitted_at = (
        DraftStatus.PENDING_APPROVAL,
        caller.user.id,
        utcnow(),
    )
    db.flush()
    if email.case_id and (case := db.get(Case, email.case_id)):
        cases.add_event(
            db,
            case,
            "submitted_for_approval",
            actor=caller.user,
            email=email,
            detail={"draft_id": str(draft.id)},
        )
    audit.record(
        db,
        "email.reply_submitted",
        actor=caller.user,
        entity_type="email",
        entity_id=email.id,
        detail={"reference": email.reference, "draft_id": str(draft.id)},
        ip=caller.ip,
    )
    db.commit()
    return _detail(db, email)


@router.post("/{email_id}/drafts/{draft_id}/reject", response_model=EmailDetailOut)
def reject_draft(
    email_id: uuid.UUID,
    draft_id: uuid.UUID,
    body: RejectIn,
    caller: Caller = Depends(require(Permission.SEND_REPLIES)),
    db: Session = Depends(get_db),
):
    email = _get(db, email_id, lock=True)
    draft = _open_draft(db, email, draft_id)
    if draft.status == DraftStatus.PENDING_APPROVAL and not has_permission(
        caller.user.role, Permission.APPROVE_REPLIES
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a supervisor can reject a submitted reply.")
    draft.status, draft.reject_reason = DraftStatus.REJECTED, body.reason
    draft.decided_by_id, draft.decided_at = caller.user.id, utcnow()
    audit.record(
        db,
        "email.draft_rejected",
        actor=caller.user,
        entity_type="email",
        entity_id=email.id,
        detail={"reference": email.reference, "draft_id": str(draft.id), "reason": body.reason},
        ip=caller.ip,
    )
    db.commit()
    return _detail(db, email)


@router.post("/{email_id}/send", response_model=EmailDetailOut)
def send_from_dashboard(
    email_id: uuid.UUID,
    body: SendIn,
    caller: Caller = Depends(require(Permission.SEND_REPLIES)),
    db: Session = Depends(get_db),
    mailbox: Mailbox = Depends(get_mailbox),
):
    """Send a reply to the customer, in their Outlook thread, and mark the email answered."""
    earlier = db.scalar(select(EmailReply).where(EmailReply.request_id == body.request_id))
    if earlier is not None:  # the same click again: never send twice
        if earlier.email_id != email_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This request id was already used.")
        return _detail(db, _get(db, email_id))
    email = _get(db, email_id, lock=True)
    if email.status != EmailStatus.QUEUED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This email is not waiting for a reply.")
    _check_outgoing(body.text)
    draft = _open_draft(db, email, body.draft_id)
    needs_supervisor = _approval(db, email) == "supervisor" or (
        draft is not None and draft.status == DraftStatus.PENDING_APPROVAL
    )
    if needs_supervisor:
        if not has_permission(caller.user.role, Permission.APPROVE_REPLIES):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "This reply needs a supervisor's approval. Submit it for approval instead.",
            )
        if draft is not None and draft.submitted_by_id == caller.user.id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Someone other than the submitter must approve it."
            )

    reply = send_reply(
        db,
        mailbox,
        email,
        ReplyKind.OFFICER,
        body.text,
        actor=caller.user,
        request_id=body.request_id,
        draft_id=draft.id if draft else None,
    )
    case = db.get(Case, email.case_id) if email.case_id else None
    if not reply.succeeded:
        audit.record(
            db,
            "email.reply_failed",
            actor=caller.user,
            entity_type="email",
            entity_id=email.id,
            detail={"reference": email.reference},
            ip=caller.ip,
        )
        db.commit()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Outlook did not send the reply. Nothing was sent; try again."
        )
    edited = draft is not None and body.text.strip() != draft.body_text.strip()
    if draft is not None:
        draft.status = DraftStatus.SENT
        draft.edited_text = body.text if edited else None
        draft.decided_by_id, draft.decided_at = caller.user.id, utcnow()
    for other in db.scalars(
        select(AiDraft).where(AiDraft.email_id == email.id, AiDraft.status.in_(DraftStatus.OPEN))
    ):
        if draft is None or other.id != draft.id:
            other.status = DraftStatus.SUPERSEDED
    email.status, email.status_reason = EmailStatus.REPLIED, "Replied from the dashboard"
    email.handled_by_id, email.handled_at, email.handled_via = caller.user.id, utcnow(), HandledVia.DASHBOARD
    if case is not None:
        cases.add_event(
            db,
            case,
            "reply_sent",
            actor=caller.user,
            email=email,
            detail={
                "draft_id": str(draft.id) if draft else None,
                "edited": edited,
                "approved": needs_supervisor,
            },
        )
        cases.sync(db, case)
    audit.record(
        db,
        "email.reply_sent",
        actor=caller.user,
        entity_type="email",
        entity_id=email.id,
        detail={
            "reference": email.reference,
            "draft_id": str(draft.id) if draft else None,
            "draft_model": draft.model if draft else None,
            "edited": edited,
            "supervisor_approval": needs_supervisor,
            "submitted_by": str(draft.submitted_by_id) if draft and draft.submitted_by_id else None,
        },
        ip=caller.ip,
    )
    db.commit()
    return _detail(db, email)
