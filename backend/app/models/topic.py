"""Topics: what customers ask about, and how to answer from the bank's own data.

A topic says:
  * how customers phrase the question (examples, used to recognise the email),
  * which of the bank's databases to read and the SELECT query to run,
  * which values to pull out of the email for that query (e.g. an account number),
  * how to lay out the query result as a reply.

The reply is always built from what the query returns; this application stores no answers.

Every change is a new revision, and a revision is used only after someone other than its
author approves it (maker-checker). The topic keeps using its last approved revision while a
newer one waits, so editing never takes a live topic offline.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, utcnow


class RevisionStatus:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    live_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topic_revisions.id", use_alter=True, name="fk_topics_live_revision")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # post_update: topics and revisions reference each other, so this link is saved in a second step.
    live_revision: Mapped[TopicRevision | None] = relationship(
        foreign_keys=[live_revision_id], post_update=True
    )
    revisions: Mapped[list[TopicRevision]] = relationship(
        back_populates="topic",
        foreign_keys="TopicRevision.topic_id",
        order_by="TopicRevision.submitted_at.desc()",
    )


class TopicRevision(Base):
    __tablename__ = "topic_revisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    examples: Mapped[list[str]] = mapped_column(JSON)

    # Where the answer comes from.
    data_source: Mapped[str] = mapped_column(String(64))
    # Guided topics: {"table", "columns", "conditions", "order_by", "many"}; the app builds the
    # SELECT itself. Advanced topics leave this empty and use a hand-written query instead.
    builder: Mapped[dict | None] = mapped_column(JSON)
    query: Mapped[str | None] = mapped_column(Text)
    # name -> regular expression; the first match in the email becomes the :name query parameter.
    extractors: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    max_rows: Mapped[int] = mapped_column(Integer, default=10)

    # How the reply is laid out. {column} placeholders are filled from the first row;
    # {rows} is replaced by row_template repeated once per returned row.
    reply_template: Mapped[str] = mapped_column(Text)
    row_template: Mapped[str | None] = mapped_column(Text)

    # For customer-specific data: only answer when Microsoft confirms the sender's address is
    # genuine (DMARC pass), because the From address of an email can be faked.
    require_verified_sender: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[str] = mapped_column(String(20), default=RevisionStatus.PENDING)
    submitted_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(500))

    topic: Mapped[Topic] = relationship(back_populates="revisions", foreign_keys=[topic_id])
    submitted_by = relationship("StaffUser", foreign_keys=[submitted_by_id], lazy="joined")
    reviewed_by = relationship("StaffUser", foreign_keys=[reviewed_by_id], lazy="joined")
