"""Telling the people in charge about emails the assistant could not answer, without flooding them.

  * Urgent: a sensitive email (fraud, complaint, ...) -> one alert straight away.
  * Overdue: still unanswered after the configured hours, and nobody has replied in Outlook
    -> one reminder per email, batched into a single message per check.
  * Daily digest: once a day at the configured time -> everything still waiting, grouped by why
    the assistant could not answer, plus the questions customers keep asking that no topic
    covers yet (ideas for new topics).
  * System: the mailbox or a bank database keeps failing -> one alert, repeated at most every
    six hours while the problem lasts.

Alerts go to the person in charge, copying the people listed in Settings (and, if switched on,
every administrator). All must be in FNB_INTERNAL_EMAIL_DOMAINS, so customer details
can never be sent outside the bank. Every message is recorded in the notification log.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, time, timedelta
from html import escape
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import as_utc, utcnow
from app.models import CustomerEmail, EmailStatus, NotificationLog, Reason, StaffUser
from app.services.app_settings import get_app_settings, get_mailbox_state
from app.services.email_text import strip_quoted_history

if TYPE_CHECKING:
    from app.services.processor import Mailbox

log = logging.getLogger(__name__)

SYSTEM_ALERT_EVERY = timedelta(hours=6)
MAILBOX_FAILURES_BEFORE_ALERT = 3
DATA_ERRORS_BEFORE_ALERT = 3
EXCERPT_CHARS = 600
_WORD = re.compile(r"[a-z]{4,}")
_COMMON = frozenset(
    "please hello dear good morning afternoon evening thank thanks regards would could kindly need "
    "want help with your from have that this about when what which there their they them will "
    "account bank email fnb".split()
)


# -- recipients --------------------------------------------------------------------------------


def allowed_recipients(recipients: list[str], settings: Settings) -> list[str]:
    domains = settings.internal_domains
    clean = sorted({r.strip().lower() for r in recipients if "@" in r})
    if not domains:
        return clean if not settings.is_production else []
    return [r for r in clean if r.rsplit("@", 1)[1] in domains]


def escalation_recipients(db: Session, settings: Settings) -> tuple[list[str], list[str]]:
    """(to, cc): the person in charge, then everyone copied (plus administrators if switched on).

    Only internal addresses survive. If nobody is set as in charge, the first copied person is.
    """
    cfg = get_app_settings(db)
    copied = list(cfg.notify_recipients or [])
    if cfg.escalation_copy_admins:
        copied += [
            u.email
            for u in db.scalars(
                select(StaffUser).where(StaffUser.role == "admin", StaffUser.is_active.is_(True))
            )
            if u.email
        ]
    primary = allowed_recipients([cfg.escalation_to] if cfg.escalation_to else [], settings)
    cc = allowed_recipients(copied, settings)
    if not primary and cc:
        primary, cc = cc[:1], cc[1:]
    return primary, [a for a in cc if a not in primary]


def _send(
    db: Session, mailbox: Mailbox, settings: Settings, kind: str, subject: str, html: str, count: int
) -> bool:
    to, cc = escalation_recipients(db, settings)
    if not to:
        return False
    entry = NotificationLog(kind=kind, subject=subject[:300], recipients=to + cc, email_count=count)
    db.add(entry)
    try:
        mailbox.send_mail(to, subject, html, cc=cc)
        entry.succeeded = True
    except Exception as exc:  # noqa: BLE001 - logged and retried at the next opportunity
        entry.error = str(exc)[:300]
        log.error("Sending %s notification failed: %s", kind, exc)
    db.commit()
    return entry.succeeded


# -- message layout ----------------------------------------------------------------------------


def _link(settings: Settings, email: CustomerEmail) -> str:
    links = []
    if settings.app_base_url:
        url = f"{settings.app_base_url.rstrip('/')}/emails/{email.id}"
        links.append(f'<a href="{escape(url)}" style="color:#00857F">Open in Smart Email Assistance</a>')
    if email.web_link:
        links.append(f'<a href="{escape(email.web_link)}" style="color:#00857F">Open in Outlook</a>')
    return " &nbsp;·&nbsp; ".join(links)


def _email_block(settings: Settings, email: CustomerEmail, *, excerpt: bool) -> str:
    received = as_utc(email.received_at).astimezone(ZoneInfo(settings.timezone)).strftime("%d %b %Y, %H:%M")
    text = ""
    if excerpt:
        question = strip_quoted_history(email.body_text)[:EXCERPT_CHARS]
        text = (
            f'<div style="margin:8px 0;padding:10px 12px;background:#F3F8F7;border-radius:8px;'
            f'white-space:pre-wrap">{escape(question)}</div>'
        )
    return (
        '<div style="padding:12px 0;border-bottom:1px solid #DCE7E5">'
        f"<strong>{escape(email.reference)}</strong> &nbsp;·&nbsp; "
        f"{escape(email.subject or '(no subject)')}<br>"
        f'<span style="color:#4A6B69">From {escape(email.from_name or email.from_address)} '
        f"&lt;{escape(email.from_address)}&gt; · received {received}</span><br>"
        f'<span style="color:#B35C00">Why it needs a person: {escape(email.status_reason or "")}</span>'
        f"{text}<div>{_link(settings, email)}</div></div>"
    )


def _wrap(title: str, intro: str, content: str) -> str:
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.5;color:#0F2B2A;'
        'max-width:680px">'
        '<div style="border-top:4px solid #00857F;padding-top:12px">'
        f'<h2 style="margin:0 0 6px 0;font-size:18px">{escape(title)}</h2>'
        f'<p style="margin:0 0 12px 0;color:#4A6B69">{intro}</p>{content}'
        '<p style="margin-top:18px;color:#6B8784;font-size:12px">Sent by Smart Email Assistance. '
        "Reply to the customer in Outlook as usual, or from the dashboard; either way the email is "
        "marked as handled.</p></div></div>"
    )


# -- the four kinds of notification ------------------------------------------------------------


def alert_urgent(db: Session, mailbox: Mailbox, email: CustomerEmail, settings: Settings) -> None:
    cfg = get_app_settings(db)
    if not cfg.notify_urgent or email.urgent_alert_at is not None:
        return
    intro = (
        "This may be fraud or a security problem. Follow the bank's urgent escalation procedure "
        "and contact the customer through verified channels. Nothing was answered automatically."
        if email.reason_code == Reason.URGENT
        else "This email mentions a sensitive topic, so the assistant did not answer it. "
        "Please look at it now."
    )
    html = _wrap("Urgent customer email", intro, _email_block(settings, email, excerpt=True))
    if _send(db, mailbox, settings, "urgent", f"[Urgent] Customer email {email.reference}", html, 1):
        email.urgent_alert_at = utcnow()
        db.commit()


def alert_quality_pause(db: Session, mailbox: Mailbox, settings: Settings, paused: list[str]) -> None:
    """The quality monitor stopped automatic answers for one or more categories."""
    cfg = get_app_settings(db)
    if not cfg.notify_system:
        return
    html = _wrap(
        "Automatic answers paused",
        "The quality monitor stopped automatic answers for these categories. Emails in them now go "
        "to people. Review them on the AI &amp; automation page and resume when it is safe.",
        "".join(f"<p>{escape(p)}</p>" for p in paused),
    )
    _send(db, mailbox, settings, "system", "Smart Email Assistance: automatic answers paused", html, 0)


def send_overdue(db: Session, mailbox: Mailbox, settings: Settings) -> int:
    cfg = get_app_settings(db)
    if not cfg.notify_overdue:
        return 0
    cutoff = utcnow() - timedelta(hours=max(1, cfg.overdue_after_hours))
    overdue = list(
        db.scalars(
            select(CustomerEmail)
            .where(
                CustomerEmail.status == EmailStatus.QUEUED,
                CustomerEmail.overdue_alert_at.is_(None),
                CustomerEmail.received_at <= cutoff,
            )
            .order_by(CustomerEmail.received_at)
            .limit(50)
        ).unique()
    )
    if not overdue:
        return 0
    n = len(overdue)
    html = _wrap(
        f"{n} customer email{'s' if n > 1 else ''} waiting more than {cfg.overdue_after_hours} hours",
        "Nobody has replied to these in Outlook yet.",
        "".join(_email_block(settings, e, excerpt=False) for e in overdue),
    )
    subject = f"{n} customer email{'s' if n > 1 else ''} overdue for a reply"
    if _send(db, mailbox, settings, "overdue", subject, html, n):
        now = utcnow()
        for e in overdue:
            e.overdue_alert_at = now
        db.commit()
    return n


def topic_gaps(db: Session, days: int = 7, limit: int = 6) -> list[tuple[str, int]]:
    """Words that keep appearing in emails no topic matched: hints for new topics."""
    since = utcnow() - timedelta(days=days)
    counts: Counter = Counter()
    for subject, body in db.execute(
        select(CustomerEmail.subject, CustomerEmail.body_text).where(
            CustomerEmail.reason_code == Reason.NO_MATCH, CustomerEmail.received_at >= since
        )
    ):
        words = set(_WORD.findall(f"{subject} {strip_quoted_history(body)[:500]}".lower())) - _COMMON
        counts.update(words)
    return [(w, c) for w, c in counts.most_common(limit) if c >= 2]


def send_digest_if_due(
    db: Session, mailbox: Mailbox, settings: Settings, now: datetime | None = None
) -> bool:
    cfg = get_app_settings(db)
    if not cfg.notify_digest:
        return False
    zone = ZoneInfo(settings.timezone)
    local = (now or utcnow()).astimezone(zone)
    state = get_mailbox_state(db)
    try:
        hour, minute = (int(x) for x in cfg.digest_time.split(":"))
    except ValueError:
        hour, minute = 16, 0
    if local.time() < time(hour, minute) or state.last_digest_on == local.date():
        return False

    start = datetime.combine(local.date(), time.min, zone)
    today = list(db.scalars(select(CustomerEmail).where(CustomerEmail.received_at >= start)).unique())
    customer = [e for e in today if e.status != EmailStatus.SKIPPED]
    auto = sum(1 for e in customer if e.status == EmailStatus.AUTO_ANSWERED)
    waiting = list(
        db.scalars(
            select(CustomerEmail)
            .where(CustomerEmail.status == EmailStatus.QUEUED)
            .order_by(CustomerEmail.received_at)
        ).unique()
    )
    groups: dict[str, list[CustomerEmail]] = {}
    for e in waiting:
        groups.setdefault(e.reason_code or Reason.NO_MATCH, []).append(e)

    rows = "".join(
        f'<tr><td style="padding:6px 12px 6px 0">{escape(Reason.LABELS.get(code, code))}</td>'
        f'<td style="padding:6px 0;text-align:right"><strong>{len(items)}</strong></td>'
        f'<td style="padding:6px 0 6px 12px;color:#4A6B69">'
        f"{escape(', '.join(e.reference for e in items[:4]))}{' …' if len(items) > 4 else ''}</td></tr>"
        for code, items in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    )
    gaps = topic_gaps(db)
    gap_html = (
        '<h3 style="font-size:15px;margin:18px 0 6px">Questions the knowledge base does not cover yet</h3>'
        '<p style="margin:0 0 6px;color:#4A6B69">Words that keep coming up in emails the assistant '
        "could not match (last 7 days). Adding them to the knowledge base would answer more emails "
        "automatically.</p>" + ", ".join(f"<strong>{escape(w)}</strong> ({c})" for w, c in gaps)
        if gaps
        else ""
    )
    intro = (
        f"Today: <strong>{len(customer)}</strong> customer emails, <strong>{auto}</strong> answered "
        f"automatically. <strong>{len(waiting)}</strong> still need a person."
    )
    table = (
        f'<table style="border-collapse:collapse;width:100%">{rows}</table>'
        if rows
        else "<p>Nothing is waiting. Well done.</p>"
    )
    html = _wrap(f"Daily summary, {local:%d %B %Y}", intro, table + gap_html)
    if _send(
        db, mailbox, settings, "digest", f"Customer emails summary for {local:%d %b}", html, len(waiting)
    ):
        state.last_digest_on = local.date()
        db.commit()
        return True
    return False


def send_system_alert_if_needed(db: Session, mailbox: Mailbox, settings: Settings) -> bool:
    cfg = get_app_settings(db)
    if not cfg.notify_system:
        return False
    state = get_mailbox_state(db)
    if state.system_alert_at and utcnow() - as_utc(state.system_alert_at) < SYSTEM_ALERT_EVERY:
        return False
    problems = []
    if (state.consecutive_failures or 0) >= MAILBOX_FAILURES_BEFORE_ALERT:
        problems.append(
            f"The Outlook mailbox could not be checked {state.consecutive_failures} times in a row "
            f"(last error: {escape(state.last_error or 'unknown')})."
        )
    data_errors = db.scalar(
        select(func.count()).where(
            CustomerEmail.reason_code == Reason.DATA_ERROR,
            CustomerEmail.received_at >= utcnow() - timedelta(hours=1),
        )
    )
    if (data_errors or 0) >= DATA_ERRORS_BEFORE_ALERT:
        problems.append(
            f"A bank database could not be read for {data_errors} emails in the last hour. "
            "Check the Data Sources page."
        )
    if not problems:
        return False
    html = _wrap(
        "Smart Email Assistance needs attention",
        "Customer emails may not be answered automatically until this is fixed.",
        "".join(f"<p>{p}</p>" for p in problems),
    )
    # Sending may itself fail if the mailbox is the problem; it is logged either way.
    sent = _send(db, mailbox, settings, "system", "Smart Email Assistance: system problem", html, 0)
    state.system_alert_at = utcnow()
    db.commit()
    return sent


def run_due(db: Session, mailbox: Mailbox, settings: Settings) -> None:
    """Called by the worker after every mailbox check."""
    for job in (send_overdue, send_digest_if_due, send_system_alert_if_needed):
        try:
            job(db, mailbox, settings)
        except Exception as exc:  # noqa: BLE001 - one failing job must not stop the others
            db.rollback()
            log.error("Notification job %s failed: %s", job.__name__, exc)
