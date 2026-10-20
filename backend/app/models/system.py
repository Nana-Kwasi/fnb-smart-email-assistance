"""Dashboard-editable settings, mailbox sync state, and the audit log."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow

DEFAULT_ACK_TEMPLATE = (
    "Dear {customer_name},\n\n"
    "Thank you for contacting FNB. We have received your email and our customer care team "
    "is attending to it.\n\n"
    "Your reference number is {reference}. Please quote it in any follow-up about this request.\n\n"
    "Kind regards,\nFNB Customer Care"
)
DEFAULT_ANSWER_HEADER = (
    "Dear {customer_name},\n\nThank you for your patience. Regarding your enquiry ({reference}):"
)
DEFAULT_ANSWER_FOOTER = (
    "If this does not fully answer your question, simply reply to this email and a member of our "
    "team will assist you.\n\nKind regards,\nFNB Customer Care"
)
# Emails mentioning these are always handed to an officer, marked high priority, and never
# answered automatically.
DEFAULT_SENSITIVE_KEYWORDS = [
    "fraud",
    "scam",
    "stolen",
    "unauthorised",
    "unauthorized",
    "hacked",
    "complaint",
    "dispute",
    "legal",
    "lawyer",
    "court",
    "ombudsman",
    "urgent",
    "lost card",
]


# "auto": answers may be sent by themselves. False: always suggested to a person first.
DEFAULT_CATEGORIES = [
    {"name": "General", "auto": True},
    {"name": "Branches", "auto": True},
    {"name": "Accounts", "auto": True},
    {"name": "Cards", "auto": True},
    {"name": "Digital banking", "auto": True},
    {"name": "Fees and charges", "auto": True},
    {"name": "Loans", "auto": False},
    {"name": "Policies and procedures", "auto": False},
]


class AppSettings(Base):
    """A single row (id = 1) of settings the admin can change from the dashboard."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    ack_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Off until an admin deliberately switches it on.
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.6)
    ack_template: Mapped[str] = mapped_column(Text, default=DEFAULT_ACK_TEMPLATE)
    answer_header: Mapped[str] = mapped_column(Text, default=DEFAULT_ANSWER_HEADER)
    answer_footer: Mapped[str] = mapped_column(Text, default=DEFAULT_ANSWER_FOOTER)
    sensitive_keywords: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: list(DEFAULT_SENSITIVE_KEYWORDS)
    )
    # Below the automatic threshold but at least this sure: the answer is suggested to staff.
    suggest_threshold: Mapped[float] = mapped_column(Float, default=0.4)
    # Knowledge base categories and whether each may be answered automatically.
    knowledge_categories: Mapped[list[dict]] = mapped_column(
        JSON, default=lambda: [dict(c) for c in DEFAULT_CATEGORIES]
    )

    # Who looks after emails the assistant cannot answer, and when to tell them.
    escalation_to: Mapped[str | None] = mapped_column(String(320))  # the person in charge
    notify_recipients: Mapped[list[str]] = mapped_column(JSON, default=list)  # people copied
    escalation_copy_admins: Mapped[bool] = mapped_column(Boolean, default=False)

    # Knowledge base
    max_upload_mb: Mapped[int] = mapped_column(Integer, default=15)
    # Four-eyes check: someone other than the uploader must publish (also applies to topics).
    require_second_person: Mapped[bool] = mapped_column(Boolean, default=False)
    # Longer answers are only ever suggested to staff, never sent by themselves.
    max_auto_answer_chars: Mapped[int] = mapped_column(Integer, default=1500)

    # Emails
    # A reply in the same thread within this many days does not get a second acknowledgement.
    follow_up_days: Mapped[int] = mapped_column(Integer, default=7)
    page_size: Mapped[int] = mapped_column(Integer, default=10)  # rows per page in lists

    # Security and data
    session_idle_minutes: Mapped[int] = mapped_column(Integer, default=30)
    retention_months: Mapped[int] = mapped_column(Integer, default=0)  # 0 = keep emails forever
    notify_urgent: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_overdue: Mapped[bool] = mapped_column(Boolean, default=True)
    overdue_after_hours: Mapped[int] = mapped_column(Integer, default=4)
    notify_digest: Mapped[bool] = mapped_column(Boolean, default=True)
    digest_time: Mapped[str] = mapped_column(String(5), default="16:00")  # HH:MM, bank time zone
    notify_system: Mapped[bool] = mapped_column(Boolean, default=True)

    # Where the bank keeps its general answers (FAQ). Set once by an admin; topics then just
    # pick an entry. key = the column that identifies a row, e.g. a code or id.
    faq_data_source: Mapped[str | None] = mapped_column(String(64))
    faq_table: Mapped[str | None] = mapped_column(String(128))
    faq_key_column: Mapped[str | None] = mapped_column(String(128))
    faq_question_column: Mapped[str | None] = mapped_column(String(128))
    faq_answer_column: Mapped[str | None] = mapped_column(String(128))

    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationLog(Base):
    """Every alert and digest sent to the people in charge."""

    __tablename__ = "notification_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))  # urgent | overdue | digest | system
    subject: Mapped[str] = mapped_column(String(300))
    recipients: Mapped[list[str]] = mapped_column(JSON)
    email_count: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class MailboxState(Base):
    """Where the worker got to in the mailbox, and whether the last sync worked (id = 1)."""

    __tablename__ = "mailbox_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    delta_link: Mapped[str | None] = mapped_column(Text)
    # Emails received before this moment are never processed (no surprise replies to old mail).
    process_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(String(500))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_reply_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_digest_on: Mapped[date | None] = mapped_column(Date)
    system_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_retention_on: Mapped[date | None] = mapped_column(Date)
    last_quality_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    actor_username: Mapped[str] = mapped_column(String(128))  # "system" for the worker
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    # Tamper evidence: each event's hash covers its content and the previous event's hash, so
    # changing or deleting an old event breaks every hash after it.
    seq: Mapped[int | None] = mapped_column(Integer, index=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str | None] = mapped_column(String(64))


class AuditChainHead(Base):
    """The latest audit hash (id = 1). Locked while an event is added, so the chain never forks."""

    __tablename__ = "audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_hash: Mapped[str | None] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer, default=0)
