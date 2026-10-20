"""Management analytics: what customers write about, where the work piles up, and what keeps
coming back.

Everything under "facts" is counted from records. Everything under "signals" is a hypothesis
from patterns in email volume and is labelled so: a spike of similar emails suggests something
worth checking, it does not prove an outage. No customer names, addresses or email text are
returned; complaint themes are word counts.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Caller, require
from app.core.config import Settings, get_settings
from app.core.db import as_utc, get_db, utcnow
from app.models import AiDraft, Case, CaseStatus, CustomerEmail, EmailStatus
from app.security.rbac import Permission
from app.services import quality
from app.services.email_text import strip_quoted_history
from app.services.policy import get_policy

router = APIRouter(prefix="/analytics", tags=["analytics"])

SPIKE_FACTOR = 3.0
SPIKE_MIN_EMAILS = 5
SPIKE_MIN_SENDERS = 3
FORECAST_MIN_DAYS = 28
_WORD = re.compile(r"[a-z]{4,}")
_STOP = frozenset(
    """please hello dear good morning afternoon evening thank thanks regards would could kindly need want
    help with your from have that this about when what which there their they them will been were also
    just still since into only some more very account bank email sent send fnb customer care today
    yesterday week days time made make know like sir madam""".split()
)


def _themes(emails: list[CustomerEmail], limit: int = 10) -> list[dict]:
    words: Counter = Counter()
    pairs: Counter = Counter()
    for e in emails:
        tokens = [
            w
            for w in _WORD.findall(f"{e.subject} {strip_quoted_history(e.body_text)}".lower())
            if w not in _STOP
        ]
        words.update(set(tokens))
        pairs.update({f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)})
    themes = [{"theme": p, "emails": n} for p, n in pairs.most_common(limit) if n >= 2]
    return themes or [{"theme": w, "emails": n} for w, n in words.most_common(limit) if n >= 2]


@router.get("")
def analytics(
    _: Caller = Depends(require(Permission.VIEW_ANALYTICS)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    days: int = Query(30, ge=7, le=180),
):
    zone = ZoneInfo(settings.timezone)
    today = datetime.now(zone).date()
    start = datetime.combine(today - timedelta(days=days - 1), time.min, zone)
    labels = {c["code"]: c["label"] for c in get_policy(db).categories or []}
    emails = [
        e
        for e in db.scalars(select(CustomerEmail).where(CustomerEmail.received_at >= start)).unique()
        if e.status != EmailStatus.SKIPPED
    ]

    def local_day(dt: datetime) -> date:
        return as_utc(dt).astimezone(zone).date()

    # -- facts ---------------------------------------------------------------------------------
    by_category = Counter(e.category or "unclassified" for e in emails)
    trend: dict[date, Counter] = defaultdict(Counter)
    for e in emails:
        trend[local_day(e.received_at)][e.category or "unclassified"] += 1
    top_categories = [c for c, _ in by_category.most_common(6)]
    daily = [
        {
            "date": (today - timedelta(days=i)).isoformat(),
            **{c: trend[today - timedelta(days=i)].get(c, 0) for c in top_categories},
        }
        for i in range(days - 1, -1, -1)
    ]

    cases = list(db.scalars(select(Case).where(Case.opened_at >= start)).unique())
    open_cases = list(db.scalars(select(Case).where(Case.status == CaseStatus.OPEN)).unique())
    workload = Counter(c.department or "Unassigned" for c in open_cases)
    case_emails = Counter(e.case_id for e in emails if e.case_id)
    resolved = [c for c in cases if c.resolved_at]
    resolution_by_cat: dict[str, list[float]] = defaultdict(list)
    for c in resolved:
        resolution_by_cat[c.category or "unclassified"].append(
            (as_utc(c.resolved_at) - as_utc(c.opened_at)).total_seconds() / 3600
        )

    # Backlog at the end of each day: emails received by then and not yet handled.
    backlog = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        waiting = sum(
            1
            for e in emails
            if as_utc(e.received_at) < end
            and (e.handled_at is None or as_utc(e.handled_at) >= end)
            and e.status != EmailStatus.SKIPPED
        )
        backlog.append({"date": day.isoformat(), "waiting": waiting})

    complaints = [e for e in emails if e.category in ("complaint", "missing_transaction")]
    repeat = sum(1 for n in case_emails.values() if n > 1)
    drafted = {d.email_id for d in db.scalars(select(AiDraft).where(AiDraft.created_at >= start))}
    effort = [n for n in case_emails.values()]

    # -- signals (hypotheses) -------------------------------------------------------------------
    signals = []
    recent_cut = utcnow() - timedelta(hours=24)
    for cat in by_category:
        recent = [
            e for e in emails if (e.category or "unclassified") == cat and as_utc(e.received_at) >= recent_cut
        ]
        older = [
            e for e in emails if (e.category or "unclassified") == cat and as_utc(e.received_at) < recent_cut
        ]
        base_days = max(1, days - 1)
        baseline = len(older) / base_days
        senders = {e.from_address.lower() for e in recent}
        if (
            len(recent) >= SPIKE_MIN_EMAILS
            and len(senders) >= SPIKE_MIN_SENDERS
            and len(recent) >= SPIKE_FACTOR * max(baseline, 1)
        ):
            signals.append(
                {
                    "kind": "possible_recurring_issue",
                    "category": cat,
                    "label": labels.get(cat, cat),
                    "detail": f"{len(recent)} emails from {len(senders)} customers in 24 hours, "
                    f"against about {baseline:.1f} a day before. Possibly a service problem: "
                    "check with the service team before treating it as one.",
                    "themes": _themes(recent, 5),
                    "hypothesis": True,
                }
            )

    forecast = None
    history_days = (today - min((local_day(e.received_at) for e in emails), default=today)).days + 1
    if history_days >= FORECAST_MIN_DAYS:
        per_weekday: dict[int, list[int]] = defaultdict(list)
        counts = Counter(local_day(e.received_at) for e in emails)
        for i in range(history_days):
            d = today - timedelta(days=i)
            per_weekday[d.weekday()].append(counts.get(d, 0))
        forecast = [
            {
                "date": (today + timedelta(days=i)).isoformat(),
                "expected": round(mean(per_weekday[(today + timedelta(days=i)).weekday()]), 1),
            }
            for i in range(1, 8)
        ]

    recommendations = []
    gap_cats = [c for c, n in by_category.most_common() if c not in ("spam", "unclassified") and n >= 5]
    q = quality.live_metrics(db, days=min(days, 30))
    for cat in gap_cats[:3]:
        m = q["categories"].get(cat, {})
        if (m.get("automation_rate") or 0) < 0.2:
            recommendations.append(
                {
                    "text": f"{labels.get(cat, cat)}: {by_category[cat]} emails, few answered automatically. "
                    "Adding approved answers for the common questions here would help most.",
                    "evidence": f"{by_category[cat]} emails; "
                    f"automation rate {(m.get('automation_rate') or 0):.0%}",
                    "hypothesis": True,
                }
            )
    reopened = [c for c in cases if c.reopened_count]
    if reopened:
        common = Counter(c.category or "unclassified" for c in reopened).most_common(1)[0]
        recommendations.append(
            {
                "text": f"Cases in {labels.get(common[0], common[0])} reopen most often; "
                "review the answers given.",
                "evidence": f"{common[1]} of {len(reopened)} reopened cases",
                "hypothesis": True,
            }
        )

    return {
        "days": days,
        "facts": {
            "emails": len(emails),
            "by_category": [
                {"category": c, "label": labels.get(c, c), "emails": n} for c, n in by_category.most_common()
            ],
            "daily_by_category": daily,
            "top_categories": [{"category": c, "label": labels.get(c, c)} for c in top_categories],
            "department_workload": [{"department": d, "open_cases": n} for d, n in workload.most_common()],
            "complaints": len(complaints),
            "complaint_themes": _themes(complaints),
            "backlog": backlog,
            "avg_resolution_hours": [
                {"category": c, "label": labels.get(c, c), "hours": round(mean(v), 1), "cases": len(v)}
                for c, v in sorted(resolution_by_cat.items(), key=lambda kv: -len(kv[1]))
            ],
            "cases_opened": len(cases),
            "reopened_cases": len(reopened),
            "repeat_contact_rate": round(repeat / len(case_emails), 3) if case_emails else None,
            "avg_emails_per_case": round(mean(effort), 2) if effort else None,
            "avg_handoffs_per_case": round(mean(c.handoffs or 0 for c in cases), 2) if cases else None,
            "possible_duplicates": sum(1 for c in cases if c.possible_duplicate_of_id),
            "ai_drafted": len(drafted),
            "quality": q,
        },
        "signals": signals,
        "forecast": forecast,
        "forecast_note": None if forecast else f"Needs at least {FORECAST_MIN_DAYS} days of history.",
        "recommendations": recommendations,
    }
