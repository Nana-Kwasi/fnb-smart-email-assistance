"""The knowledge base: documents the bank gives us to answer general customer questions.

A document (e.g. "Card FAQ") has versions. Each uploaded file is a new version; its text is
split into entries (a question and its answer, or a heading and the text under it). Only the
published version of a document is used, and only within its effective and expiry dates.
Replies are an entry's text, word for word: nothing is generated.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, utcnow


class VersionStatus:
    DRAFT = "draft"  # uploaded, can be tested, not used for emails yet
    PUBLISHED = "published"  # in use
    ARCHIVED = "archived"  # replaced by a newer version, or withdrawn


class Audience:
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"

    ALL = (PUBLIC, INTERNAL, RESTRICTED)


class EntryKind:
    QA = "qa"  # an explicit question and answer
    SECTION = "section"  # a heading and the text under it


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60))
    # public: may be quoted to customers. internal: staff guidance, never in a customer answer.
    # restricted: internal, and only supervisors, admins and auditors may open it.
    audience: Mapped[str] = mapped_column(String(12), default="public")
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list[KnowledgeVersion]] = relationship(
        back_populates="document",
        order_by="KnowledgeVersion.uploaded_at.desc()",
        cascade="all, delete-orphan",
    )


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"))
    version_label: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), default=VersionStatus.DRAFT)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    file_data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)  # the original, for audit
    effective_from: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(500))
    warnings: Mapped[str | None] = mapped_column(Text)  # what the reader could not use
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("staff_users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[KnowledgeDocument] = relationship(back_populates="versions")
    entries: Mapped[list[KnowledgeEntry]] = relationship(
        back_populates="version", order_by="KnowledgeEntry.position", cascade="all, delete-orphan"
    )
    uploaded_by = relationship("StaffUser", foreign_keys=[uploaded_by_id], lazy="joined")
    published_by = relationship("StaffUser", foreign_keys=[published_by_id], lazy="joined")


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(500))  # the question or heading
    content: Mapped[str] = mapped_column(Text)  # the answer: sent word for word

    version: Mapped[KnowledgeVersion] = relationship(back_populates="entries")
