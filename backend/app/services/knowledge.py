"""Knowledge base rules: uploading versions, publishing (maker-checker), and what is live."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.db import utcnow
from app.models import (
    AppSettings,
    Audience,
    KnowledgeDocument,
    KnowledgeEntry,
    KnowledgeVersion,
    StaffUser,
    VersionStatus,
)
from app.services import audit
from app.services.app_settings import get_app_settings
from app.services.knowledge_reader import CONTENT_TYPES, examples_for, extension, read
from app.services.matcher import Candidate


class KnowledgeRuleError(Exception):
    """Breaks a knowledge base rule. The message is safe to show."""


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def is_in_date(version: KnowledgeVersion, on: date | None = None) -> bool:
    on = on or today()
    return (version.effective_from is None or version.effective_from <= on) and (
        version.expires_on is None or version.expires_on >= on
    )


def category_is_automatic(cfg: AppSettings, category: str) -> bool:
    for c in cfg.knowledge_categories or []:
        if c.get("name", "").lower() == category.lower():
            return bool(c.get("auto"))
    return False  # an unknown category is never answered by itself


def add_version(
    db: Session,
    actor: StaffUser,
    document: KnowledgeDocument,
    *,
    filename: str,
    data: bytes,
    version_label: str | None = None,
    effective_from: date | None,
    expires_on: date | None,
    notes: str | None,
    ip: str | None = None,
) -> KnowledgeVersion:
    if effective_from and expires_on and expires_on < effective_from:
        raise KnowledgeRuleError("The expiry date is before the effective date.")
    parsed = read(filename, data, max_bytes=get_app_settings(db).max_upload_mb * 1024 * 1024)
    label = (version_label or "").strip() or str(len(document.versions) + 1)
    if any(v.version_label == label for v in document.versions):
        raise KnowledgeRuleError(f"Version {label} already exists for this document.")
    version = KnowledgeVersion(
        document=document,
        version_label=label,
        status=VersionStatus.DRAFT,
        filename=filename[:255],
        content_type=CONTENT_TYPES[extension(filename)],
        size_bytes=len(data),
        file_data=data,
        effective_from=effective_from,
        expires_on=expires_on,
        notes=(notes or "").strip()[:500] or None,
        warnings="\n".join(parsed.warnings[:50]) or None,
        uploaded_by_id=actor.id,
    )
    version.entries = [
        KnowledgeEntry(position=i, kind=e.kind, title=e.title, content=e.content)
        for i, e in enumerate(parsed.entries)
    ]
    db.add(version)
    db.flush()
    audit.record(
        db,
        "knowledge.uploaded",
        actor=actor,
        entity_type="knowledge",
        entity_id=document.id,
        detail={
            "document": document.name,
            "version": label,
            "file": filename,
            "entries": len(parsed.entries),
        },
        ip=ip,
    )
    return version


def publish(db: Session, actor: StaffUser, version: KnowledgeVersion, ip: str | None = None) -> None:
    if version.status != VersionStatus.DRAFT:
        raise KnowledgeRuleError("Only a draft can be published.")
    if version.uploaded_by_id == actor.id and get_app_settings(db).require_second_person:
        raise KnowledgeRuleError(
            "You uploaded this version, so someone else must publish it (Settings: second-person check)."
        )
    for other in version.document.versions:
        if other.status == VersionStatus.PUBLISHED:
            other.status = VersionStatus.ARCHIVED
    version.status, version.published_by_id, version.published_at = (
        VersionStatus.PUBLISHED,
        actor.id,
        utcnow(),
    )
    audit.record(
        db,
        "knowledge.published",
        actor=actor,
        entity_type="knowledge",
        entity_id=version.document_id,
        detail={"document": version.document.name, "version": version.version_label},
        ip=ip,
    )


def archive(db: Session, actor: StaffUser, version: KnowledgeVersion, ip: str | None = None) -> None:
    if version.status == VersionStatus.ARCHIVED:
        raise KnowledgeRuleError("This version is already archived.")
    version.status = VersionStatus.ARCHIVED
    audit.record(
        db,
        "knowledge.archived",
        actor=actor,
        entity_type="knowledge",
        entity_id=version.document_id,
        detail={"document": version.document.name, "version": version.version_label},
        ip=ip,
    )


def live_versions(db: Session, *, include_drafts: bool = False) -> list[KnowledgeVersion]:
    """Versions the assistant answers from: each document's published version, within its dates.

    include_drafts (testing only): use each document's newest draft instead, where there is one.
    """
    docs = db.scalars(
        select(KnowledgeDocument).options(
            selectinload(KnowledgeDocument.versions).selectinload(KnowledgeVersion.entries)
        )
    ).all()
    chosen = []
    for doc in docs:
        draft = next((v for v in doc.versions if v.status == VersionStatus.DRAFT), None)
        published = next((v for v in doc.versions if v.status == VersionStatus.PUBLISHED), None)
        version = draft if include_drafts and draft else published
        if version and (include_drafts or is_in_date(version)):
            chosen.append(version)
    return chosen


def knowledge_candidates(db: Session, *, include_drafts: bool = False) -> list[Candidate]:
    """Entries that may be sent to customers: public documents only (internal guidance never is)."""
    return [
        Candidate(e.id, v.id, e.title, examples_for(e.kind, e.title, e.content), kind="knowledge")
        for v in live_versions(db, include_drafts=include_drafts)
        if v.document.audience == Audience.PUBLIC
        for e in v.entries
    ]
