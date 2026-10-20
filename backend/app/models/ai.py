"""The automation policy, AI drafts, model call statistics and evaluation runs.

The policy is one row (id = 1) that decides what the assistant may do on its own. Every change
to it is kept in ``policy_changes`` with who made it, why, and the values before and after.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, utcnow


class Mode:
    """How far the assistant may go with answers it writes itself (staged roll-out)."""

    DRAFT_ONLY = "draft_only"  # AI drafts are shown to officers; nothing it writes is sent by itself
    SUPERVISED = "supervised"  # as draft-only, and drafts that pass every check are marked ready to send
    RESTRICTED = "restricted"  # AI answers may be sent by themselves in low-risk permitted categories
    EXPANDED = "expanded"  # also medium-risk permitted categories, no daily cap

    ALL = (DRAFT_ONLY, SUPERVISED, RESTRICTED, EXPANDED)
    AUTONOMOUS = (RESTRICTED, EXPANDED)
    LABELS = {
        DRAFT_ONLY: "Draft only",
        SUPERVISED: "Supervised pilot",
        RESTRICTED: "Restricted autonomous",
        EXPANDED: "Expanded autonomous",
    }


def _category(
    code, label, department, *, auto=False, route="queue", sla=24, risk="low", ack=True, approval="officer"
):
    return {
        "code": code,
        "label": label,
        "department": department,
        "ack": ack,  # send the acknowledgement
        "auto_send": auto,  # an answer may be sent without a person (still subject to mode and checks)
        "approval": approval,  # who must approve a draft before it is sent: officer | supervisor
        "risk_tier": risk,  # low | medium | high; restricted mode only sends "low"
        "min_evidence": 0.5,  # retrieval score the best source must reach
        "route": route,  # queue | urgent
        "sla_hours": sla,
        "max_send_attempts": 3,
        "paused": False,  # automatic answers stopped for this category (by a person or the QA monitor)
        "paused_reason": None,
    }


DEFAULT_CATEGORY_POLICIES = [
    _category("general", "General enquiry", "Customer Care", auto=True),
    _category("account_info", "Accounts and banking services", "Customer Care", auto=True),
    _category("digital_banking", "Mobile and internet banking", "Digital Banking", auto=True),
    _category("cards", "Card services", "Cards", auto=True, risk="medium"),
    _category("payments", "Transfers and payments", "Payments", risk="medium"),
    _category("fees_rates", "Fees, charges and rates", "Customer Care", auto=True),
    _category("branches", "Branches and opening hours", "Customer Care", auto=True),
    _category("complaint", "Complaint or dissatisfaction", "Complaints", risk="high", approval="supervisor"),
    _category("missing_transaction", "Missing or delayed transaction", "Payments", risk="high", sla=8),
    _category(
        "fraud",
        "Suspected fraud or unauthorised activity",
        "Fraud & Security",
        risk="high",
        route="urgent",
        sla=2,
        approval="supervisor",
    ),
    _category(
        "account_security",
        "Account security and access",
        "Fraud & Security",
        risk="high",
        route="urgent",
        sla=4,
        approval="supervisor",
    ),
    _category("identity_verification", "Needs identity verification", "Customer Care", risk="high"),
    _category("staff_action", "Needs action by staff", "Operations", risk="medium"),
    _category("unclear", "Incomplete or unclear", "Customer Care", risk="medium"),
    _category("spam", "Spam or out of scope", "Customer Care", ack=False, risk="high"),
]
CATEGORY_CODES = tuple(c["code"] for c in DEFAULT_CATEGORY_POLICIES)
DEPARTMENTS = sorted({c["department"] for c in DEFAULT_CATEGORY_POLICIES})


class AutomationPolicy(Base):
    __tablename__ = "automation_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(16), default=Mode.DRAFT_ONLY)
    # Emergency stop: no answer of any kind is sent by itself. Reception, acknowledgements,
    # case records and the dashboard keep working.
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_switch_reason: Mapped[str | None] = mapped_column(String(300))
    categories: Mapped[list[dict]] = mapped_column(
        JSON, default=lambda: [dict(c) for c in DEFAULT_CATEGORY_POLICIES]
    )
    # Languages the assistant may answer by itself; anything else goes to qualified staff.
    auto_languages: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["en"])
    # Emails from the bank's own domains: acknowledge them like customers' (usually not).
    ack_internal_senders: Mapped[bool] = mapped_column(Boolean, default=False)
    # Restricted mode: most AI answers sent by themselves per category per day.
    restricted_daily_cap: Mapped[int] = mapped_column(Integer, default=50)
    # QA monitor: pause a category's automatic answers when too many customers write back.
    auto_pause_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_pause_min_samples: Mapped[int] = mapped_column(Integer, default=10)
    auto_pause_reopen_rate: Mapped[float] = mapped_column(Float, default=0.2)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PolicyChange(Base):
    __tablename__ = "policy_changes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    actor_username: Mapped[str] = mapped_column(String(128))  # "system" for the QA monitor
    version: Mapped[int] = mapped_column(Integer)
    changes: Mapped[dict] = mapped_column(JSON)  # {field: {"before": ..., "after": ...}}
    reason: Mapped[str] = mapped_column(String(500))


class ModelCall(Base):
    """One request to the language model. No prompt or answer text is kept here."""

    __tablename__ = "model_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    task: Mapped[str] = mapped_column(String(20))  # classify | draft | health
    model: Mapped[str] = mapped_column(String(120))
    ok: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(120))  # kind of failure only
    email_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_emails.id", ondelete="SET NULL"))


class DraftStatus:
    PROPOSED = "proposed"  # written by the assistant, waiting for an officer
    EDITED = "edited"  # changed by an officer, not sent yet
    PENDING_APPROVAL = "pending_approval"  # submitted; a supervisor must approve and send
    SENT = "sent"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"  # the email was answered another way

    OPEN = (PROPOSED, EDITED, PENDING_APPROVAL)


class AiDraft(Base):
    __tablename__ = "ai_drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer_emails.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default=DraftStatus.PROPOSED)
    body_text: Mapped[str] = mapped_column(Text)  # as the assistant wrote it, with greeting and sign-off
    edited_text: Mapped[str | None] = mapped_column(Text)
    # [{n, entry_id, version_id, document, version, title}] - the exact sources and versions used
    citations: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # [{check, passed, detail}] - the independent checks, run on the assistant's text
    validation: Mapped[list[dict]] = mapped_column(JSON, default=list)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str] = mapped_column(String(120))
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(String(500))

    submitted_by = relationship("StaffUser", foreign_keys=[submitted_by_id], lazy="joined")
    decided_by = relationship("StaffUser", foreign_keys=[decided_by_id], lazy="joined")


class EvalRun(Base):
    """Aggregate results of one evaluation or replay run. No email text is stored."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    dataset: Mapped[str] = mapped_column(String(200))
    models: Mapped[dict] = mapped_column(JSON)  # {"fast": ..., "strong": ...}
    policy_version: Mapped[int] = mapped_column(Integer)
    cases: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSON)
    run_by: Mapped[str] = mapped_column(String(128))
