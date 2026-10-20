"""Continuous quality monitoring of what the assistant does, and automatic pausing.

Live signals (measured from what actually happened, no labels needed):
  * reopened after an automatic answer: the customer wrote again in the same case within 7 days
  * possible missed escalation: a case answered automatically later turned urgent (fraud/security)
  * validation failures, model errors and latency, officer edits to AI drafts, citation coverage

Accuracy, correct/unnecessary escalation and unsafe auto-send *rates* need labelled emails:
those come from the evaluation set (``python -m app.evals.run``), shown next to these.

When ``auto_pause_enabled`` is on, a category's automatic answers are paused (and the people in
charge told) if: its reopen rate passes the limit over enough samples, or any possible missed
escalation is seen. Pausing is recorded as a policy change by "system"; a person resumes it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from statistics import median
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import as_utc, utcnow
from app.models import AiDraft, CustomerEmail, DraftStatus, EmailReply, EmailStatus, ModelCall, ReplyKind
from app.services import notifications
from app.services.app_settings import get_mailbox_state
from app.services.policy import change_policy, get_policy

if TYPE_CHECKING:
    from app.services.processor import Mailbox

log = logging.getLogger(__name__)

CHECK_EVERY = timedelta(hours=1)
WINDOW = timedelta(days=7)
URGENT_CATEGORIES = ("fraud", "account_security")


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def live_metrics(db: Session, days: int = 7) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    emails = list(db.scalars(select(CustomerEmail).where(CustomerEmail.received_at >= since)).unique())
    by_case: dict = defaultdict(list)
    for e in emails:
        if e.case_id:
            by_case[e.case_id].append(e)

    per_category: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    missed: list[str] = []
    for e in emails:
        cat = e.category or "unclassified"
        per_category[cat]["emails"] += 1
        if e.status == EmailStatus.QUEUED or e.reason_code:
            per_category[cat]["to_person"] += 1
        if e.reason_code == "urgent":
            per_category[cat]["urgent"] += 1
        if e.status != EmailStatus.AUTO_ANSWERED:
            continue
        per_category[cat]["auto"] += 1
        answered_at = as_utc(e.handled_at or e.received_at)
        later = [
            o
            for o in by_case.get(e.case_id, [])
            if o.id != e.id and answered_at < as_utc(o.received_at) <= answered_at + timedelta(days=7)
        ]
        if later:
            per_category[cat]["reopened"] += 1
        if any(o.category in URGENT_CATEGORIES for o in later):
            per_category[cat]["missed_escalation"] += 1
            missed.append(e.reference)

    drafts = list(db.scalars(select(AiDraft).where(AiDraft.created_at >= since)).unique())
    sent_ai = list(
        db.scalars(
            select(EmailReply).where(
                EmailReply.created_at >= since,
                EmailReply.kind == ReplyKind.AI_ANSWER,
                EmailReply.succeeded.is_(True),
            )
        ).unique()
    )
    officer_from_draft = list(
        db.scalars(
            select(EmailReply).where(
                EmailReply.created_at >= since,
                EmailReply.kind == ReplyKind.OFFICER,
                EmailReply.draft_id.is_not(None),
                EmailReply.succeeded.is_(True),
            )
        ).unique()
    )
    draft_by_id = {d.id: d for d in drafts}
    edited = sum(
        1
        for r in officer_from_draft
        if r.draft_id in draft_by_id and r.body_text.strip() != draft_by_id[r.draft_id].body_text.strip()
    )
    calls = list(db.scalars(select(ModelCall).where(ModelCall.at >= since)))
    per_model: dict[str, dict[str, Any]] = {}
    for model in sorted({c.model for c in calls}):
        mine = [c for c in calls if c.model == model]
        lat = sorted(c.latency_ms for c in mine if c.ok)
        per_model[model] = {
            "calls": len(mine),
            "errors": sum(1 for c in mine if not c.ok),
            "error_rate": _pct(sum(1 for c in mine if not c.ok), len(mine)),
            "latency_p50_ms": int(median(lat)) if lat else None,
            "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else None,
        }
    failed_checks: dict[str, int] = defaultdict(int)
    for d in drafts:
        for chk in d.validation or []:
            if not chk.get("passed"):
                failed_checks[chk.get("check", "?")] += 1

    categories = {
        cat: {
            **counts,
            "reopen_rate": _pct(counts.get("reopened", 0), counts.get("auto", 0)),
            "automation_rate": _pct(counts.get("auto", 0), counts.get("emails", 0)),
        }
        for cat, counts in per_category.items()
    }
    return {
        "days": days,
        "categories": categories,
        "possible_missed_escalations": missed[:20],
        "drafts": {
            "created": len(drafts),
            "passed_checks": sum(1 for d in drafts if d.passed),
            "validation_failure_rate": _pct(sum(1 for d in drafts if not d.passed), len(drafts)),
            "failed_checks": dict(failed_checks),
            "sent_by_officers": len(officer_from_draft),
            "edited_by_officers": edited,
            "officer_edit_rate": _pct(edited, len(officer_from_draft)),
            "rejected": sum(1 for d in drafts if d.status == DraftStatus.REJECTED),
        },
        "ai_answers_sent": len(sent_ai),
        "citation_coverage": _pct(
            sum(1 for r in sent_ai if r.draft_id in draft_by_id and draft_by_id[r.draft_id].citations),
            len(sent_ai),
        ),
        "models": per_model,
    }


def check_if_due(db: Session, mailbox: Mailbox, settings: Settings) -> list[str]:
    state = get_mailbox_state(db)
    now = utcnow()
    if state.last_quality_check_at and now - as_utc(state.last_quality_check_at) < CHECK_EVERY:
        return []
    state.last_quality_check_at = now
    db.commit()
    return enforce(db, mailbox, settings)


def enforce(db: Session, mailbox: Mailbox | None, settings: Settings) -> list[str]:
    """Pause automatic answers for categories that trip a critical condition. Returns their codes."""
    policy = get_policy(db)
    if not policy.auto_pause_enabled:
        return []
    metrics = live_metrics(db, days=WINDOW.days)["categories"]
    paused: list[tuple[str, str]] = []
    categories = [dict(c) for c in policy.categories or []]
    for cat in categories:
        if cat.get("paused") or not cat.get("auto_send"):
            continue
        m = metrics.get(cat["code"], {})
        reason = None
        if m.get("missed_escalation"):
            reason = "an automatically answered case later turned out to be urgent (fraud or security)"
        elif (
            m.get("auto", 0) >= policy.auto_pause_min_samples
            and (m.get("reopen_rate") or 0) > policy.auto_pause_reopen_rate
        ):
            reason = (
                f"{m['reopen_rate']:.0%} of automatic answers were followed by another email "
                f"(limit {policy.auto_pause_reopen_rate:.0%})"
            )
        if reason:
            cat["paused"], cat["paused_reason"] = True, f"Paused by the quality monitor: {reason}"
            paused.append((cat["code"], f"{cat['label']}: {reason}"))
    if not paused:
        return []
    change_policy(
        db, {"categories": categories}, reason="Quality monitor: " + "; ".join(p[1] for p in paused)
    )
    db.commit()
    log.warning("Quality monitor paused automatic answers for: %s", ", ".join(p[0] for p in paused))
    if mailbox is not None:
        notifications.alert_quality_pause(db, mailbox, settings, [p[1] for p in paused])
    return [p[0] for p in paused]
