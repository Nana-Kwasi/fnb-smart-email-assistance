"""Customer emails pulled from the Outlook mailbox, and every reply sent to them."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, utcnow


class EmailStatus:
    RECEIVED = "received"  # stored, not processed yet
    QUEUED = "queued"  # waiting for an officer
    AUTO_ANSWERED = "auto_answered"  # an approved answer was sent automatically
    REPLIED = "replied"  # an officer replied
    CLOSED = "closed"  # an officer closed it without replying
    SKIPPED = "skipped"  # not a customer email (auto-reply, bounce, our own mailbox)

    OPEN = (RECEIVED, QUEUED)


class AckStatus:
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    DISABLED = "disabled"
    NOT_APPLICABLE = "n/a"


class Reason:
    """Why the assistant could not answer by itself. Drives the dashboard and the digest."""

    NO_MATCH = "no_match"  # no topic fits the question
    AMBIGUOUS = "ambiguous"  # two topics fit equally well
    SENSITIVE = "sensitive"  # fraud, complaint, ... always for a person
    MISSING_INFO = "missing_info"  # e.g. the account number was not in the email
    UNVERIFIED_SENDER = "unverified_sender"
    NO_DATA = "no_data"  # the database had no matching record
    DATA_ERROR = "data_error"  # the database could not be read
    AUTO_OFF = "auto_off"  # automatic answers are switched off
    CUSTOMER_DATA_OFF = "customer_data_off"
    SUGGEST_ONLY = "suggest_only"  # found in the knowledge base, but its category is "suggest only"
    BELOW_AUTO = "below_auto"  # found, but not sure enough to send by itself
    ANSWER_TOO_LONG = "answer_too_long"  # customer-specific answers not enabled (phase 1)
    SEND_FAILED = "send_failed"
    NO_AUTO_REPLY = "no_auto_reply"  # sender asked for no automatic replies
    INTERRUPTED = "interrupted"
    AUTOMATED = "automated"  # not a customer email (skipped)
    KILL_SWITCH = "kill_switch"  # automatic answers stopped (emergency switch)
    POLICY = "policy"  # this category may not be answered automatically
    URGENT = "urgent"  # suspected fraud or account takeover: restricted urgent handling
    DRAFT_READY = "draft_ready"  # the assistant drafted an answer for an officer to check
    VALIDATION_FAILED = "validation_failed"  # the assistant's draft failed a safety check
    MODEL_UNAVAILABLE = "model_unavailable"
    PROMPT_INJECTION = "prompt_injection"  # the email tries to instruct the assistant
    LANGUAGE = "language"  # language not approved for automatic answers
    INTERNAL_SENDER = "internal_sender"
    ATTACHMENT = "attachment"  # attachments need a person (unscanned, unsafe or unreadable)

    LABELS = {
        NO_MATCH: "No topic for this question",
        AMBIGUOUS: "Unclear which topic applies",
        SENSITIVE: "Sensitive topic (fraud, complaint…)",
        MISSING_INFO: "Details missing from the email",
        UNVERIFIED_SENDER: "Sender could not be verified",
        NO_DATA: "No matching record in the database",
        DATA_ERROR: "Database could not be read",
        AUTO_OFF: "Automatic answers switched off",
        CUSTOMER_DATA_OFF: "Needs the customer's own records (not enabled yet)",
        SUGGEST_ONLY: "Answer found; its category needs a person to send it",
        BELOW_AUTO: "Answer found, but not sure enough to send by itself",
        ANSWER_TOO_LONG: "Answer found, but too long to send by itself",
        SEND_FAILED: "Outlook did not send the reply",
        NO_AUTO_REPLY: "Sender asked for no automatic replies",
        INTERRUPTED: "Processing was interrupted",
        AUTOMATED: "Not a customer email",
        KILL_SWITCH: "Automatic answers stopped (emergency switch)",
        POLICY: "Category needs a person",
        URGENT: "Urgent: possible fraud or security issue",
        DRAFT_READY: "Draft ready to check",
        VALIDATION_FAILED: "Draft failed a safety check",
        MODEL_UNAVAILABLE: "AI model unavailable",
        PROMPT_INJECTION: "Email tries to instruct the assistant",
        LANGUAGE: "Language needs qualified staff",
        INTERNAL_SENDER: "Sent from inside the bank",
        ATTACHMENT: "Attachments need a person",
    }


class HandledVia:
    AUTOMATIC = "automatic"
    OUTLOOK = "outlook"  # someone replied in Outlook; detected automatically
    DASHBOARD = "dashboard"  # marked handled in the dashboard


class Priority:
    NORMAL = "normal"
    HIGH = "high"


class ReplyKind:
    ACKNOWLEDGEMENT = "acknowledgement"
    AUTO_ANSWER = "auto_answer"  # approved text sent by itself (knowledge base or bank data)
    AI_ANSWER = "ai_answer"  # an AI-written answer sent by itself (restricted/expanded mode only)
    OFFICER = "officer"  # sent from the dashboard by a person


class CustomerEmail(Base):
    __tablename__ = "customer_emails"
    __table_args__ = (Index("ix_customer_emails_status_received", "status", "received_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    graph_message_id: Mapped[str] = mapped_column(String(512), unique=True)
    conversation_id: Mapped[str | None] = mapped_column(String(512))
    from_address: Mapped[str] = mapped_column(String(320), index=True)
    from_name: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(998), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(20), default=EmailStatus.RECEIVED)
    status_reason: Mapped[str | None] = mapped_column(String(255))
    reason_code: Mapped[str | None] = mapped_column(String(32), index=True)
    # A later email in a thread we already acknowledged (no second acknowledgement is sent).
    is_follow_up: Mapped[bool] = mapped_column(default=False)
    # Opens the email in Outlook on the web.
    web_link: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10), default=Priority.NORMAL)
    ack_status: Mapped[str] = mapped_column(String(10), default=AckStatus.PENDING)

    # Whether Microsoft confirmed the sender address is genuine (DMARC pass).
    sender_verified: Mapped[bool] = mapped_column(default=False)

    # What the matcher decided. Kept even when nothing was sent, for the audit trail.
    match_topic_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topics.id", ondelete="SET NULL"))
    match_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topic_revisions.id", ondelete="SET NULL")
    )
    match_score: Mapped[float | None] = mapped_column(Float)
    # The knowledge base entry that answered (or would answer) this email, and its version.
    knowledge_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_entries.id", ondelete="SET NULL")
    )
    knowledge_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="SET NULL"), index=True
    )

    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    handled_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handled_via: Mapped[str | None] = mapped_column(String(16))
    handled_note: Mapped[str | None] = mapped_column(String(500))
    # When the people in charge were told about it (each alert is sent at most once).
    urgent_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overdue_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Acknowledgement retries (a failed acknowledgement is tried again with growing waits).
    ack_attempts: Mapped[int] = mapped_column(Integer, default=0)
    ack_last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Classification, validated against the allowed values before it is stored.
    category: Mapped[str | None] = mapped_column(String(32), index=True)
    intent: Mapped[str | None] = mapped_column(String(200))
    language: Mapped[str | None] = mapped_column(String(8))
    urgency: Mapped[str | None] = mapped_column(String(10))
    risk: Mapped[str | None] = mapped_column(String(10))
    sentiment: Mapped[str | None] = mapped_column(String(12))
    department: Mapped[str | None] = mapped_column(String(60))
    ai_summary: Mapped[str | None] = mapped_column(Text)
    # Evidence quotes, flags, suggested next steps; and which model (or "rules") decided.
    classification: Mapped[dict | None] = mapped_column(JSON)
    classified_by: Mapped[str | None] = mapped_column(String(120))
    # The route decided by the policy engine, with every reason, for the audit trail.
    decision: Mapped[dict | None] = mapped_column(JSON)

    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"), index=True)

    match_revision = relationship("TopicRevision", lazy="joined")
    knowledge_entry = relationship("KnowledgeEntry", lazy="joined")
    assigned_to = relationship("StaffUser", foreign_keys=[assigned_to_id], lazy="joined")
    handled_by = relationship("StaffUser", foreign_keys=[handled_by_id], lazy="joined")
    replies: Mapped[list[EmailReply]] = relationship(back_populates="email", order_by="EmailReply.created_at")
    attachments: Mapped[list[Attachment]] = relationship(back_populates="email", order_by="Attachment.name")


class EmailReply(Base):
    """An exact copy of what was sent (or attempted), so the audit trail never has to guess."""

    __tablename__ = "email_replies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_emails.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    body_text: Mapped[str] = mapped_column(Text)
    revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("topic_revisions.id"))
    sent_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    succeeded: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # A send from the dashboard carries a key chosen by the browser, so a double click or a
    # retried request can never send the same reply twice.
    request_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    draft_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ai_drafts.id", ondelete="SET NULL"))

    email: Mapped[CustomerEmail] = relationship(back_populates="replies")
    sent_by = relationship("StaffUser", lazy="joined")


class ScanStatus:
    CLEAN = "clean"
    INFECTED = "infected"
    NOT_SCANNED = "not_scanned"  # no scanner configured: never opened by the assistant
    ERROR = "error"
    REJECTED = "rejected"  # type or size not allowed


class Attachment(Base):
    """A customer's attachment: what it is, whether it is safe, and the text read from it.

    The file itself is not kept (staff open it in Outlook). Extracted text is untrusted data.
    """

    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer_emails.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    scan_status: Mapped[str] = mapped_column(String(12))
    scan_detail: Mapped[str | None] = mapped_column(String(300))
    extracted_text: Mapped[str | None] = mapped_column(Text)
    problems: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    email: Mapped[CustomerEmail] = relationship(back_populates="attachments")
