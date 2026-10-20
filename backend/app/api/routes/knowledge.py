"""Knowledge base: upload the bank's documents, publish them, and test what they would answer."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload, undefer

from app.api.deps import Caller, require
from app.core.db import get_db
from app.models import (
    Audience,
    CustomerEmail,
    EmailStatus,
    KnowledgeDocument,
    KnowledgeEntry,
    KnowledgeVersion,
    VersionStatus,
)
from app.schemas import UserOut
from app.security.rbac import Permission, has_permission
from app.services import audit
from app.services import knowledge as svc
from app.services.app_settings import get_app_settings
from app.services.grounding import knowledge_conflicts
from app.services.knowledge_reader import UnreadableDocument
from app.services.matcher import Matcher
from app.services.processor import find_sensitive

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
AudienceValue = Literal["public", "internal", "restricted"]
EXPIRY_WARNING_DAYS = 30


# -- shapes ------------------------------------------------------------------------------------


class VersionOut(BaseModel):
    id: uuid.UUID
    version_label: str
    status: str
    filename: str
    size_bytes: int
    effective_from: date | None
    expires_on: date | None
    in_date: bool
    notes: str | None
    warnings: list[str]
    entry_count: int
    answered: int  # emails answered automatically from this version
    suggested: int  # emails where it was suggested to staff
    uploaded_by: UserOut
    uploaded_at: datetime
    published_by: UserOut | None
    published_at: datetime | None


class DocumentSummary(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    audience: str
    status: str  # published | draft | archived
    live_version: str | None
    has_draft: bool
    entry_count: int
    answered: int
    updated_at: datetime


class DocumentDetail(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    audience: str
    versions: list[VersionOut]


class EntryOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    content: str


class DocumentPatch(BaseModel):
    name: Text | None = None
    category: Text | None = None
    audience: AudienceValue | None = None


class HealthDoc(BaseModel):
    id: uuid.UUID
    name: str
    version: str | None
    expires_on: date | None
    problem: str


class KnowledgeHealthOut(BaseModel):
    documents_in_use: int
    entries_in_use: int
    index: str
    expired: list[HealthDoc]
    expiring_soon: list[HealthDoc]
    drafts_waiting: list[HealthDoc]
    conflicts: list[str]


class TryIn(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=5000)]
    include_drafts: bool = True


class TryHit(BaseModel):
    title: str
    content: str
    document: str
    document_id: uuid.UUID
    version: str
    version_status: str
    score: float


class TryOut(BaseModel):
    outcome: str  # automatic | suggested | person
    explanation: str
    hits: list[TryHit]


# -- helpers -----------------------------------------------------------------------------------


def _usage(db: Session, version_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
    if not version_ids:
        return {}
    rows = db.execute(
        select(CustomerEmail.knowledge_version_id, CustomerEmail.status, func.count())
        .where(CustomerEmail.knowledge_version_id.in_(version_ids))
        .group_by(CustomerEmail.knowledge_version_id, CustomerEmail.status)
    ).all()
    out: dict[uuid.UUID, list[int]] = {}
    for vid, st, n in rows:
        counts = out.setdefault(vid, [0, 0])
        counts[0 if st == EmailStatus.AUTO_ANSWERED else 1] += n
    return {k: (v[0], v[1]) for k, v in out.items()}


def _entry_counts(db: Session, version_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not version_ids:
        return {}
    return dict(
        db.execute(
            select(KnowledgeEntry.version_id, func.count())
            .where(KnowledgeEntry.version_id.in_(version_ids))
            .group_by(KnowledgeEntry.version_id)
        ).all()
    )


def _can_see(caller: Caller | None, doc: KnowledgeDocument) -> bool:
    return doc.audience != Audience.RESTRICTED or (
        caller is not None and has_permission(caller.user.role, Permission.VIEW_RESTRICTED_KNOWLEDGE)
    )


def _load(db: Session, doc_id: uuid.UUID, caller: Caller | None = None) -> KnowledgeDocument:
    doc = db.scalar(
        select(KnowledgeDocument)
        .options(selectinload(KnowledgeDocument.versions))
        .where(KnowledgeDocument.id == doc_id)
    )
    # Restricted documents do not exist for people who may not open them.
    if doc is None or (caller is not None and not _can_see(caller, doc)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found.")
    return doc


def _version(db: Session, version_id: uuid.UUID, caller: Caller | None = None) -> KnowledgeVersion:
    version = db.get(KnowledgeVersion, version_id)
    if version is None or (caller is not None and not _can_see(caller, version.document)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found.")
    return version


def _detail(db: Session, doc: KnowledgeDocument) -> DocumentDetail:
    ids = [v.id for v in doc.versions]
    usage, counts = _usage(db, ids), _entry_counts(db, ids)
    return DocumentDetail(
        id=doc.id,
        name=doc.name,
        category=doc.category,
        audience=doc.audience,
        versions=[
            VersionOut(
                id=v.id,
                version_label=v.version_label,
                status=v.status,
                filename=v.filename,
                size_bytes=v.size_bytes,
                effective_from=v.effective_from,
                expires_on=v.expires_on,
                in_date=svc.is_in_date(v),
                notes=v.notes,
                warnings=(v.warnings or "").splitlines(),
                entry_count=counts.get(v.id, 0),
                answered=usage.get(v.id, (0, 0))[0],
                suggested=usage.get(v.id, (0, 0))[1],
                uploaded_by=UserOut.model_validate(v.uploaded_by),
                uploaded_at=v.uploaded_at,
                published_by=UserOut.model_validate(v.published_by) if v.published_by else None,
                published_at=v.published_at,
            )
            for v in doc.versions
        ],
    )


def _check_category(db: Session, category: str, caller: Caller | None = None) -> str:
    """An existing category, or (when uploading) a new one. New categories start as
    "suggest only", so nothing is sent automatically until an admin decides otherwise."""
    cfg = get_app_settings(db)
    name = " ".join(category.split())
    match = next(
        (c["name"] for c in cfg.knowledge_categories or [] if c["name"].lower() == name.lower()), None
    )
    if match:
        return match
    if caller is None or len(name) < 2:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Choose one of the categories in Settings.")
    cfg.knowledge_categories = [*(cfg.knowledge_categories or []), {"name": name[:60], "auto": False}]
    audit.record(db, "settings.category_added", actor=caller.user, detail={"category": name}, ip=caller.ip)
    return name[:60]


async def _read_upload(file: UploadFile, db: Session) -> bytes:
    limit = get_app_settings(db).max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"The file is larger than the {limit // 1048576} MB limit set in Settings.",
        )
    return data


def _add_version(db: Session, caller: Caller, doc: KnowledgeDocument, file: UploadFile, data: bytes, **kw):
    try:
        return svc.add_version(
            db, caller.user, doc, filename=file.filename or "upload", data=data, ip=caller.ip, **kw
        )
    except (UnreadableDocument, svc.KnowledgeRuleError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None


# -- routes ------------------------------------------------------------------------------------


@router.get("", response_model=list[DocumentSummary])
def list_documents(
    caller: Caller = Depends(require(Permission.VIEW_KNOWLEDGE)), db: Session = Depends(get_db)
):
    docs = [
        d
        for d in db.scalars(select(KnowledgeDocument).options(selectinload(KnowledgeDocument.versions))).all()
        if _can_see(caller, d)
    ]
    all_ids = [v.id for d in docs for v in d.versions]
    usage, counts = _usage(db, all_ids), _entry_counts(db, all_ids)
    out = []
    for d in docs:
        live = next((v for v in d.versions if v.status == VersionStatus.PUBLISHED), None)
        draft = next((v for v in d.versions if v.status == VersionStatus.DRAFT), None)
        shown = live or draft or (d.versions[0] if d.versions else None)
        out.append(
            DocumentSummary(
                id=d.id,
                name=d.name,
                category=d.category,
                audience=d.audience,
                status=VersionStatus.PUBLISHED
                if live
                else VersionStatus.DRAFT
                if draft
                else VersionStatus.ARCHIVED,
                live_version=live.version_label if live else None,
                has_draft=draft is not None,
                entry_count=counts.get(shown.id, 0) if shown else 0,
                answered=sum(usage.get(v.id, (0, 0))[0] for v in d.versions),
                updated_at=d.versions[0].uploaded_at if d.versions else d.created_at,
            )
        )
    return sorted(out, key=lambda s: (s.category.lower(), s.name.lower()))


@router.post("", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form(min_length=1, max_length=200)],
    category: Annotated[str, Form(min_length=1, max_length=60)],
    effective_from: Annotated[date | None, Form()] = None,
    expires_on: Annotated[date | None, Form()] = None,
    notes: Annotated[str | None, Form(max_length=500)] = None,
    audience: Annotated[AudienceValue, Form()] = "public",
    caller: Caller = Depends(require(Permission.EDIT_KNOWLEDGE)),
    db: Session = Depends(get_db),
):
    data = await _read_upload(file, db)
    doc = KnowledgeDocument(
        name=name.strip(),
        category=_check_category(db, category, caller),
        audience=audience,
        created_by_id=caller.user.id,
    )
    db.add(doc)
    _add_version(
        db,
        caller,
        doc,
        file,
        data,
        effective_from=effective_from,
        expires_on=expires_on,
        notes=notes,
    )
    db.commit()
    return _detail(db, _load(db, doc.id))


@router.get("/health", response_model=KnowledgeHealthOut)
def knowledge_health(
    caller: Caller = Depends(require(Permission.VIEW_KNOWLEDGE)), db: Session = Depends(get_db)
):
    """Expired and expiring documents, drafts waiting, and documents that contradict each other."""
    today = svc.today()
    soon = today + timedelta(days=EXPIRY_WARNING_DAYS)
    docs = [
        d
        for d in db.scalars(select(KnowledgeDocument).options(selectinload(KnowledgeDocument.versions))).all()
        if _can_see(caller, d)
    ]
    expired, expiring, drafts = [], [], []
    for d in docs:
        live = next((v for v in d.versions if v.status == VersionStatus.PUBLISHED), None)
        draft = next((v for v in d.versions if v.status == VersionStatus.DRAFT), None)
        if live and live.expires_on and live.expires_on < today:
            expired.append(
                HealthDoc(
                    id=d.id,
                    name=d.name,
                    version=live.version_label,
                    expires_on=live.expires_on,
                    problem="Expired: no longer used to answer customers",
                )
            )
        elif live and live.expires_on and live.expires_on <= soon:
            expiring.append(
                HealthDoc(
                    id=d.id,
                    name=d.name,
                    version=live.version_label,
                    expires_on=live.expires_on,
                    problem=f"Expires within {EXPIRY_WARNING_DAYS} days",
                )
            )
        if draft:
            drafts.append(
                HealthDoc(
                    id=d.id,
                    name=d.name,
                    version=draft.version_label,
                    expires_on=draft.expires_on,
                    problem="Draft waiting to be published",
                )
            )
    in_use = svc.live_versions(db)
    return KnowledgeHealthOut(
        documents_in_use=len(in_use),
        entries_in_use=sum(len(v.entries) for v in in_use),
        index="Up to date: the assistant re-reads published documents on every mailbox check, "
        "so a published change is used within a minute. No model retraining is involved.",
        expired=expired,
        expiring_soon=expiring,
        drafts_waiting=drafts,
        conflicts=knowledge_conflicts(db),
    )


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(
    doc_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.VIEW_KNOWLEDGE)),
    db: Session = Depends(get_db),
):
    return _detail(db, _load(db, doc_id, caller))


@router.patch("/{doc_id}", response_model=DocumentDetail)
def rename_document(
    doc_id: uuid.UUID,
    body: DocumentPatch,
    caller: Caller = Depends(require(Permission.EDIT_KNOWLEDGE)),
    db: Session = Depends(get_db),
):
    doc = _load(db, doc_id, caller)
    if body.name:
        doc.name = body.name
    if body.category:
        doc.category = _check_category(db, body.category)
    if body.audience:
        doc.audience = body.audience
    audit.record(
        db,
        "knowledge.renamed",
        actor=caller.user,
        entity_type="knowledge",
        entity_id=doc.id,
        detail=body.model_dump(exclude_none=True),
        ip=caller.ip,
    )
    db.commit()
    return _detail(db, doc)


@router.post("/{doc_id}/versions", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED)
async def upload_version(
    doc_id: uuid.UUID,
    file: Annotated[UploadFile, File()],
    effective_from: Annotated[date | None, Form()] = None,
    expires_on: Annotated[date | None, Form()] = None,
    notes: Annotated[str | None, Form(max_length=500)] = None,
    caller: Caller = Depends(require(Permission.EDIT_KNOWLEDGE)),
    db: Session = Depends(get_db),
):
    data = await _read_upload(file, db)
    doc = _load(db, doc_id, caller)
    for v in doc.versions:  # one draft at a time: a new upload replaces an unpublished one
        if v.status == VersionStatus.DRAFT:
            v.status = VersionStatus.ARCHIVED
    _add_version(
        db,
        caller,
        doc,
        file,
        data,
        effective_from=effective_from,
        expires_on=expires_on,
        notes=notes,
    )
    db.commit()
    db.expire_all()
    return _detail(db, _load(db, doc_id))


@router.get("/versions/{version_id}/entries", response_model=list[EntryOut])
def version_entries(
    version_id: uuid.UUID,
    q: str | None = Query(None, max_length=100),
    caller: Caller = Depends(require(Permission.VIEW_KNOWLEDGE)),
    db: Session = Depends(get_db),
):
    stmt = select(KnowledgeEntry).where(KnowledgeEntry.version_id == _version(db, version_id, caller).id)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(KnowledgeEntry.title).like(like) | func.lower(KnowledgeEntry.content).like(like)
        )
    return db.scalars(stmt.order_by(KnowledgeEntry.position).limit(500)).all()


@router.get("/versions/{version_id}/file")
def download_file(
    version_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.VIEW_KNOWLEDGE)),
    db: Session = Depends(get_db),
):
    version = db.scalar(
        select(KnowledgeVersion)
        .options(undefer(KnowledgeVersion.file_data))
        .where(KnowledgeVersion.id == version_id)
    )
    if version is None or not _can_see(caller, version.document):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found.")
    audit.record(
        db,
        "knowledge.downloaded",
        actor=caller.user,
        entity_type="knowledge",
        entity_id=version.document_id,
        detail={"version": version.version_label},
        ip=caller.ip,
    )
    db.commit()
    safe_name = "".join(c for c in version.filename if c.isalnum() or c in "._- ")[:120] or "document"
    return Response(
        version.file_data,
        media_type=version.content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.post("/versions/{version_id}/publish", response_model=DocumentDetail)
def publish_version(
    version_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.PUBLISH_KNOWLEDGE)),
    db: Session = Depends(get_db),
):
    version = _version(db, version_id, caller)
    try:
        svc.publish(db, caller.user, version, ip=caller.ip)
    except svc.KnowledgeRuleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return _detail(db, _load(db, version.document_id))


@router.post("/versions/{version_id}/archive", response_model=DocumentDetail)
def archive_version(
    version_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.EDIT_KNOWLEDGE)),
    db: Session = Depends(get_db),
):
    version = _version(db, version_id, caller)
    try:
        svc.archive(db, caller.user, version, ip=caller.ip)
    except svc.KnowledgeRuleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return _detail(db, _load(db, version.document_id))


@router.post("/try", response_model=TryOut)
def try_question(
    body: TryIn, _: Caller = Depends(require(Permission.VIEW_KNOWLEDGE)), db: Session = Depends(get_db)
):
    """What the knowledge base would do with a customer's question. Nothing is sent."""
    cfg = get_app_settings(db)
    matcher = Matcher(svc.knowledge_candidates(db, include_drafts=body.include_drafts))
    ranking = matcher.rank("", body.question)[:3]
    entries = {
        e.id: e
        for e in db.scalars(
            select(KnowledgeEntry).where(KnowledgeEntry.id.in_([r.topic_id for r in ranking]))
        )
    }
    hits = []
    for r in ranking:
        e = entries[r.topic_id]
        hits.append(
            TryHit(
                title=e.title,
                content=e.content,
                document=e.version.document.name,
                document_id=e.version.document_id,
                version=e.version.version_label,
                version_status=e.version.status,
                score=r.score,
            )
        )
    sensitive = find_sensitive(body.question, list(cfg.sensitive_keywords or []))
    top = ranking[0] if ranking else None
    close = len(ranking) > 1 and top and top.score - ranking[1].score < 0.05
    if sensitive:
        outcome, why = "person", f"Mentions {', '.join(sensitive)}, so it always goes to a person."
    elif not top or top.score < min(cfg.suggest_threshold, cfg.confidence_threshold):
        outcome, why = "person", "Nothing in the knowledge base matches closely enough."
    elif close:
        outcome, why = "suggested", "Two answers match about equally; a person would choose."
    else:
        doc = entries[top.topic_id].version.document
        if not cfg.auto_reply_enabled:
            outcome, why = (
                "suggested",
                "Automatic answers are switched off, so this would be suggested to staff.",
            )
        elif top.score < cfg.confidence_threshold:
            outcome, why = (
                "suggested",
                (
                    f"{top.score:.0%} sure; {cfg.confidence_threshold:.0%} is needed to send by itself, "
                    "so it would be suggested to staff."
                ),
            )
        elif not svc.category_is_automatic(cfg, doc.category):
            outcome, why = "suggested", f"{doc.category} answers are set to “suggest only” in Settings."
        elif len(entries[top.topic_id].content) > cfg.max_auto_answer_chars:
            outcome, why = "suggested", "The answer is too long to send by itself."
        else:
            outcome, why = "automatic", f"{top.score:.0%} sure: this answer would be sent automatically."
    if outcome != "person" and hits and hits[0].version_status == VersionStatus.DRAFT:
        why += " (This answer is still a draft: publish it before it is used for real emails.)"
    return TryOut(outcome=outcome, explanation=why, hits=hits)
