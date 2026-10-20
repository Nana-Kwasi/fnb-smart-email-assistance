"""The home dashboard: numbers, trends, and a plain-language briefing built from them.

The briefing is written from the live numbers by simple rules (no AI text generation), so it
is always accurate and no data leaves the bank.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Caller, require
from app.core.config import Settings, get_settings
from app.core.db import as_utc, get_db, utcnow
from app.models import (
    AckStatus,
    AiDraft,
    Case,
    CaseStatus,
    CustomerEmail,
    DataSource,
    EmailStatus,
    Priority,
    Reason,
    ReplyKind,
    RevisionStatus,
    TopicRevision,
)
from app.schemas import HealthItem, Kpi, OverviewOut, ReasonCount, RecentEmail, TopicCount
from app.security.rbac import Permission, has_permission
from app.services.app_settings import get_app_settings, get_mailbox_state
from app.services.knowledge import live_versions
from app.services.notifications import topic_gaps
from app.services.policy import get_policy

SUBSTANTIVE = (ReplyKind.AUTO_ANSWER, ReplyKind.AI_ANSWER, ReplyKind.OFFICER)


def _first_response_seconds(e: CustomerEmail) -> float | None:
    """Time to the first real answer (an acknowledgement does not count)."""
    times = [as_utc(r.created_at) for r in e.replies if r.succeeded and r.kind in SUBSTANTIVE]
    if e.handled_via == "outlook" and e.handled_at:
        times.append(as_utc(e.handled_at))
    return (min(times) - as_utc(e.received_at)).total_seconds() if times else None


def _operations(db: Session, emails: list[CustomerEmail], start: datetime) -> dict:
    """Period figures with fixed definitions, so an acknowledgement, an answer and a resolved
    case are never counted as the same thing."""
    ids = [e.id for e in emails]
    drafted = (
        set(
            db.scalars(
                select(AiDraft.email_id).where(
                    AiDraft.email_id.in_(ids),
                    AiDraft.model.not_like("%staff%"),
                    AiDraft.model.not_like("template%"),
                )
            )
        )
        if ids
        else set()
    )
    firsts = [x for x in (_first_response_seconds(e) for e in emails) if x is not None]
    resolved = list(db.scalars(select(Case).where(Case.resolved_at >= start, Case.resolved_at.is_not(None))))
    resolution = [(as_utc(c.resolved_at) - as_utc(c.opened_at)).total_seconds() for c in resolved]
    now = utcnow()
    open_cases = list(db.scalars(select(Case).where(Case.status == CaseStatus.OPEN)))
    incoming = len(emails)
    auto = sum(1 for e in emails if e.status == EmailStatus.AUTO_ANSWERED)
    return {
        "incoming": incoming,
        "acknowledged": sum(1 for e in emails if e.ack_status == AckStatus.SENT),
        "ai_drafted": len(drafted),
        "auto_resolved": auto,
        "answered_by_people": sum(1 for e in emails if e.status == EmailStatus.REPLIED),
        "awaiting_review": db.scalar(select(func.count()).where(CustomerEmail.status == EmailStatus.QUEUED))
        or 0,
        "urgent_escalations": sum(1 for e in emails if e.reason_code == Reason.URGENT),
        "avg_first_response_minutes": round(sum(firsts) / len(firsts) / 60, 1) if firsts else None,
        "avg_resolution_hours": round(sum(resolution) / len(resolution) / 3600, 1) if resolution else None,
        "automation_rate": round(auto / incoming, 3) if incoming else None,
        "open_cases": len(open_cases),
        "sla_breached": sum(
            1
            for c in open_cases
            if c.sla_due_at and as_utc(c.sla_due_at) < now and c.first_response_at is None
        ),
        "definitions": {
            "incoming": "Customer emails received (automatic messages excluded)",
            "acknowledged": "Received an acknowledgement (not an answer)",
            "ai_drafted": "The AI wrote a draft answer",
            "auto_resolved": "Answered without a person",
            "awaiting_review": "Waiting for a person now",
            "avg_first_response_minutes": "Received to first real answer (acknowledgements excluded)",
            "avg_resolution_hours": "Case opened to resolved, for cases resolved in the period",
            "automation_rate": "Answered without a person ÷ incoming",
            "sla_breached": "Open cases past their deadline with no answer yet",
        },
    }


router = APIRouter(prefix="/overview", tags=["overview"])

NEEDED_A_PERSON = (EmailStatus.QUEUED, EmailStatus.REPLIED, EmailStatus.CLOSED)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@router.get("", response_model=OverviewOut)
def overview(
    caller: Caller = Depends(require(Permission.VIEW_OVERVIEW)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    days: int = Query(14, ge=7, le=30),
) -> OverviewOut:
    zone = ZoneInfo(settings.timezone)
    today = datetime.now(zone).date()
    start = datetime.combine(today - timedelta(days=days - 1), time.min, zone)
    cfg = get_app_settings(db)

    emails = [
        e
        for e in db.scalars(select(CustomerEmail).where(CustomerEmail.received_at >= start)).unique()
        if e.status != EmailStatus.SKIPPED
    ]
    by_day: dict = {}
    for e in emails:
        by_day.setdefault(as_utc(e.received_at).astimezone(zone).date(), []).append(e)

    def day_stats(day):
        mails = by_day.get(day, [])
        auto = sum(1 for e in mails if e.status == EmailStatus.AUTO_ANSWERED)
        people = sum(1 for e in mails if e.status in NEEDED_A_PERSON)
        acks = [
            (as_utc(r.created_at) - as_utc(e.received_at)).total_seconds()
            for e in mails
            for r in e.replies
            if r.kind == ReplyKind.ACKNOWLEDGEMENT and r.succeeded
        ]
        return {
            "received": len(mails),
            "auto": auto,
            "people": people,
            "rate": round(auto / len(mails), 3) if mails else None,
            "ack": round(sum(acks) / len(acks), 1) if acks else None,
        }

    series = [
        (today - timedelta(days=i), day_stats(today - timedelta(days=i))) for i in range(days - 1, -1, -1)
    ]
    last7 = series[-7:]
    t, y = series[-1][1], series[-2][1]

    cutoff = utcnow() - timedelta(hours=cfg.overdue_after_hours)
    waiting_q = CustomerEmail.status == EmailStatus.QUEUED
    waiting = db.scalar(select(func.count()).where(waiting_q)) or 0
    urgent = db.scalar(select(func.count()).where(waiting_q, CustomerEmail.priority == Priority.HIGH)) or 0
    overdue = db.scalar(select(func.count()).where(waiting_q, CustomerEmail.received_at <= cutoff)) or 0

    week_start = datetime.combine(today - timedelta(days=6), time.min, zone)
    week = [e for e in emails if as_utc(e.received_at) >= week_start]
    reasons = Counter(e.reason_code for e in week if e.status in NEEDED_A_PERSON and e.reason_code)
    topics = Counter(
        (
            e.knowledge_entry.title
            if e.knowledge_entry
            else e.match_revision.title
            if e.match_revision
            else None
        )
        for e in week
        if e.status == EmailStatus.AUTO_ANSWERED
    )
    topics.pop(None, None)
    gaps = topic_gaps(db)
    pending = db.scalar(select(func.count()).where(TopicRevision.status == RevisionStatus.PENDING)) or 0

    # -- health ------------------------------------------------------------------------------
    state = get_mailbox_state(db)
    health = [
        HealthItem(
            name="Outlook mailbox",
            ok=None if not settings.graph_configured else state.last_poll_ok,
            detail=(
                "Not connected yet"
                if not settings.graph_configured
                else (state.last_error or "Checked recently")
                if state.last_poll_ok is not False
                else f"Not reachable: {state.last_error or 'unknown error'}"
            ),
        )
    ]
    for ds in db.scalars(select(DataSource).where(DataSource.is_active.is_(True)).order_by(DataSource.name)):
        health.append(
            HealthItem(
                name=f"Database: {ds.name}",
                ok=ds.last_check_ok,
                detail="Connected" if ds.last_check_ok else (ds.last_error or "Not tested yet"),
            )
        )
    if settings.llm_enabled:
        from app.services.llm import get_model

        model_ok, model_detail = get_model(settings).cached_health()
        health.append(HealthItem(name="AI model", ok=model_ok, detail=model_detail))
    else:
        health.append(HealthItem(name="AI model", ok=None, detail="Not switched on"))
    health.append(
        HealthItem(
            name="Attachment scanning",
            ok=True if settings.clamd_host else None,
            detail="ClamAV" if settings.clamd_host else "No scanner: attachments are not opened",
        )
    )
    live_docs = len(live_versions(db))
    health.append(
        HealthItem(
            name="Knowledge base",
            ok=True if live_docs else None,
            detail=_plural(live_docs, "published document") if live_docs else "Nothing published yet",
        )
    )
    health.append(
        HealthItem(
            name="Alerts to people in charge",
            ok=True if cfg.notify_recipients else None,
            detail=_plural(len(cfg.notify_recipients), "recipient")
            if cfg.notify_recipients
            else "Not set up yet",
        )
    )

    # -- briefing ----------------------------------------------------------------------------
    briefing = []
    if t["received"]:
        line = f"Today so far: {_plural(t['received'], 'customer email')}, {t['auto']} answered automatically"
        line += f" ({round(100 * t['rate'])}%)." if t["rate"] is not None else "."
        if y["received"]:
            line += f" Yesterday there were {y['received']} in total."
        briefing.append(line)
    else:
        briefing.append("No customer emails yet today.")
    if waiting:
        parts = [f"{_plural(waiting, 'email')} need a person"]
        if urgent:
            parts.append(f"{urgent} urgent")
        if overdue:
            parts.append(f"{overdue} waiting over {cfg.overdue_after_hours} hours")
        briefing.append(", ".join(parts) + ".")
    else:
        briefing.append("Nothing is waiting for a person.")
    if reasons:
        code, n = reasons.most_common(1)[0]
        briefing.append(
            f"Most common reason a person was needed this week: {Reason.LABELS.get(code, code).lower()} "
            f"({_plural(n, 'email')})."
        )
    if gaps:
        briefing.append(
            f"Customers keep asking about “{gaps[0][0]}”, and nothing in the knowledge base covers it yet. "
            "Adding it would answer these automatically."
        )
    policy = get_policy(db)
    if policy.kill_switch:
        briefing.append(
            "Emergency stop is on: no answer is sent automatically. Emails still arrive and are acknowledged."
        )
    elif not cfg.auto_reply_enabled:
        briefing.append("Automatic answers are switched off: only acknowledgements are being sent.")
    if pending and has_permission(caller.user.role, Permission.APPROVE_TOPICS):
        briefing.append(f"{_plural(pending, 'topic change')} waiting for your approval.")
    if not (cfg.escalation_to or cfg.notify_recipients) and has_permission(
        caller.user.role, Permission.EDIT_SETTINGS
    ):
        briefing.append("Nobody receives alerts yet. Add the people in charge under Settings.")
    broken = [h.name for h in health if h.ok is False]
    if broken:
        briefing.append("Needs attention: " + ", ".join(broken) + ".")

    recent = list(
        db.scalars(
            select(CustomerEmail)
            .where(CustomerEmail.status != EmailStatus.SKIPPED)
            .order_by(CustomerEmail.received_at.desc())
            .limit(6)
        ).unique()
    )
    db.commit()
    return OverviewOut(
        briefing=briefing,
        received=Kpi(value=t["received"], previous=y["received"], spark=[s["received"] for _, s in last7]),
        auto_rate=Kpi(value=t["rate"], previous=y["rate"], spark=[s["rate"] or 0 for _, s in last7]),
        waiting=Kpi(value=waiting, previous=None, spark=[s["people"] for _, s in last7]),
        avg_ack_seconds=Kpi(value=t["ack"], previous=y["ack"], spark=[s["ack"] or 0 for _, s in last7]),
        waiting_high_priority=urgent,
        overdue=overdue,
        overdue_after_hours=cfg.overdue_after_hours,
        trend=[
            {"date": d.isoformat(), "received": s["received"], "auto": s["auto"], "people": s["people"]}
            for d, s in series
        ],
        reasons_7d=[
            ReasonCount(code=c, label=Reason.LABELS.get(c, c), count=n) for c, n in reasons.most_common(6)
        ],
        top_topics_7d=[TopicCount(title=title, count=n) for title, n in topics.most_common(5)],
        topic_gaps=[TopicCount(title=w, count=n) for w, n in gaps],
        recent=[
            RecentEmail(
                id=e.id,
                reference=e.reference,
                from_name=e.from_name or e.from_address,
                subject=e.subject,
                received_at=e.received_at,
                status=e.status,
                priority=e.priority,
            )
            for e in recent
        ],
        health=health,
        pending_approvals=pending,
        auto_reply_enabled=cfg.auto_reply_enabled,
        operations=_operations(db, emails, start),
        automation_mode=policy.mode,
        kill_switch=policy.kill_switch,
    )
