"""Cases: one customer issue, across every email about it, with its timeline and notes.

Emails join a case only by approved rules: the same Outlook conversation, or the same
Microsoft 365-verified sender address in the same category while the case is open. Names and
unverified addresses never merge anything.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, utcnow


class CaseStatus:
    OPEN = "open"
    RESOLVED = "resolved"  # every email answered; reopens if the customer writes again
    CLOSED = "closed"  # closed by staff


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    customer_address: Mapped[str] = mapped_column(String(320), index=True)
    customer_name: Mapped[str] = mapped_column(String(255), default="")
    sender_verified: Mapped[bool] = mapped_column(default=False)
    conversation_id: Mapped[str | None] = mapped_column(String(512), index=True)
    status: Mapped[str] = mapped_column(String(12), default=CaseStatus.OPEN, index=True)
    category: Mapped[str | None] = mapped_column(String(32), index=True)
    department: Mapped[str | None] = mapped_column(String(60), index=True)
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reopened_count: Mapped[int] = mapped_column(Integer, default=0)
    handoffs: Mapped[int] = mapped_column(Integer, default=0)  # reassignments and escalations
    # Another open case from the same verified sender and category; shown, never merged.
    possible_duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"))

    assigned_to = relationship("StaffUser", lazy="joined")
    events: Mapped[list[CaseEvent]] = relationship(back_populates="case", order_by="CaseEvent.at")


class CaseEvent(Base):
    """One step in a case's timeline. ``internal`` notes are never sent to the customer."""

    __tablename__ = "case_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    actor_name: Mapped[str] = mapped_column(String(255), default="Assistant")
    email_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_emails.id", ondelete="SET NULL"))
    text: Mapped[str | None] = mapped_column(Text)  # internal note text
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    case: Mapped[Case] = relationship(back_populates="events")


class ActionStatus:
    PENDING_APPROVAL = "pending_approval"
    DONE = "done"
    REJECTED = "rejected"
    FAILED = "failed"


class CaseAction(Base):
    """A workflow action on a case (assign department, schedule a callback, ...)."""

    __tablename__ = "case_actions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[str | None] = mapped_column(String(500))
    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id"))
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requested_by = relationship("StaffUser", foreign_keys=[requested_by_id], lazy="joined")
    decided_by = relationship("StaffUser", foreign_keys=[decided_by_id], lazy="joined")
