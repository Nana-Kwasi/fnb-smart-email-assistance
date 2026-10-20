from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Caller, require
from app.api.services import get_sources
from app.core.db import get_db, utcnow
from app.models import CustomerEmail, EmailStatus, RevisionStatus, Topic, TopicRevision
from app.schemas import (
    ActiveIn,
    ReviewIn,
    RevisionOut,
    TopicDetailOut,
    TopicIn,
    TopicSummaryOut,
    TryTopicIn,
    TryTopicOut,
)
from app.security.rbac import Permission
from app.services import audit
from app.services import topics as svc
from app.services.answer_builder import build_answer
from app.services.app_settings import get_app_settings
from app.services.datasources import BUILTIN_PARAMS, DataSources

router = APIRouter(prefix="/topics", tags=["topics"])


def _load(db: Session, topic_id: uuid.UUID) -> Topic:
    topic = db.scalar(select(Topic).options(selectinload(Topic.revisions)).where(Topic.id == topic_id))
    if topic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found.")
    return topic


def _detail(topic: Topic) -> TopicDetailOut:
    pending = next((r for r in topic.revisions if r.status == RevisionStatus.PENDING), None)
    return TopicDetailOut(
        id=topic.id,
        is_active=topic.is_active,
        live=RevisionOut.model_validate(topic.live_revision) if topic.live_revision else None,
        pending=RevisionOut.model_validate(pending) if pending else None,
        history=[RevisionOut.model_validate(r) for r in topic.revisions],
    )


def _draft(body: TopicIn) -> svc.TopicDraft:
    return svc.TopicDraft(**body.model_dump())


@router.get("", response_model=list[TopicSummaryOut])
def list_topics(_: Caller = Depends(require(Permission.VIEW_TOPICS)), db: Session = Depends(get_db)):
    topics = db.scalars(
        select(Topic).options(selectinload(Topic.revisions), selectinload(Topic.live_revision))
    )
    answered = dict(
        db.execute(
            select(CustomerEmail.match_topic_id, func.count())
            .where(
                CustomerEmail.status == EmailStatus.AUTO_ANSWERED,
                CustomerEmail.received_at >= utcnow() - timedelta(days=7),
            )
            .group_by(CustomerEmail.match_topic_id)
        ).all()
    )
    out = []
    for t in topics:
        latest = t.revisions[0] if t.revisions else None
        shown = t.live_revision or latest
        if shown is None:
            continue
        out.append(
            TopicSummaryOut(
                id=t.id,
                title=shown.title,
                data_source=shown.data_source,
                is_active=t.is_active,
                is_live=t.live_revision is not None,
                has_pending=any(r.status == RevisionStatus.PENDING for r in t.revisions),
                example_count=len(shown.examples),
                updated_at=latest.submitted_at if latest else t.created_at,
                answered_7d=answered.get(t.id, 0),
            )
        )
    return sorted(out, key=lambda s: s.title.lower())


@router.get("/parameters", response_model=list[str])
def builtin_parameters(_: Caller = Depends(require(Permission.VIEW_TOPICS))):
    """Values every query can use without an extractor."""
    return list(BUILTIN_PARAMS)


@router.post("/try", response_model=TryTopicOut)
def try_topic(
    body: TryTopicIn,
    caller: Caller = Depends(require(Permission.RUN_TEST_QUERIES)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    """Build the reply a topic (not yet saved) would send for a sample email. Nothing is saved or sent."""
    try:
        draft = svc.validate_draft(db, _draft(body.topic), sources)
    except svc.TopicRuleError as exc:
        return TryTopicOut(text=None, reason=str(exc))
    revision = TopicRevision(
        title=draft.title,
        examples=draft.examples,
        data_source=draft.data_source,
        builder=draft.builder,
        query=draft.query,
        extractors=draft.extractors,
        max_rows=draft.max_rows,
        reply_template=draft.reply_template,
        row_template=draft.row_template,
        require_verified_sender=draft.require_verified_sender,
    )
    sample = body.sample
    email = CustomerEmail(
        reference="FNB-TEST-000000",
        from_address=sample.sender_email,
        from_name=sample.sender_name,
        subject=sample.subject,
        body_text=sample.body,
        received_at=datetime.now(UTC),
        sender_verified=sample.verified,
    )
    built = build_answer(db, revision, email, get_app_settings(db), sources)
    audit.record(
        db,
        "topic.tried",
        actor=caller.user,
        entity_type="topic",
        detail={"title": draft.title, "sender_email": sample.sender_email, "found": built.text is not None},
        ip=caller.ip,
    )
    db.commit()
    return TryTopicOut(text=built.text, reason=built.reason)


@router.get("/{topic_id}", response_model=TopicDetailOut)
def get_topic(
    topic_id: uuid.UUID, _: Caller = Depends(require(Permission.VIEW_TOPICS)), db: Session = Depends(get_db)
):
    return _detail(_load(db, topic_id))


@router.post("", response_model=TopicDetailOut, status_code=status.HTTP_201_CREATED)
def create_topic(
    body: TopicIn,
    caller: Caller = Depends(require(Permission.EDIT_TOPICS)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    try:
        revision = svc.propose(db, caller.user, _draft(body), sources, ip=caller.ip)
    except svc.TopicRuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    db.commit()
    return _detail(_load(db, revision.topic_id))


@router.put("/{topic_id}", response_model=TopicDetailOut)
def update_topic(
    topic_id: uuid.UUID,
    body: TopicIn,
    caller: Caller = Depends(require(Permission.EDIT_TOPICS)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    topic = _load(db, topic_id)
    try:
        svc.propose(db, caller.user, _draft(body), sources, topic=topic, ip=caller.ip)
    except svc.TopicRuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    db.commit()
    db.expire_all()
    return _detail(_load(db, topic_id))


@router.post("/{topic_id}/revisions/{revision_id}/approve", response_model=TopicDetailOut)
def approve_revision(
    topic_id: uuid.UUID,
    revision_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.APPROVE_TOPICS)),
    db: Session = Depends(get_db),
):
    topic = _load(db, topic_id)
    try:
        svc.approve(db, caller.user, topic, revision_id, ip=caller.ip)
    except svc.TopicRuleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    db.expire_all()
    return _detail(_load(db, topic_id))


@router.post("/{topic_id}/revisions/{revision_id}/reject", response_model=TopicDetailOut)
def reject_revision(
    topic_id: uuid.UUID,
    revision_id: uuid.UUID,
    body: ReviewIn,
    caller: Caller = Depends(require(Permission.APPROVE_TOPICS)),
    db: Session = Depends(get_db),
):
    topic = _load(db, topic_id)
    try:
        svc.reject(db, caller.user, topic, revision_id, body.note, ip=caller.ip)
    except svc.TopicRuleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    db.expire_all()
    return _detail(_load(db, topic_id))


@router.post("/{topic_id}/active", response_model=TopicDetailOut)
def set_active(
    topic_id: uuid.UUID,
    body: ActiveIn,
    caller: Caller = Depends(require(Permission.APPROVE_TOPICS)),
    db: Session = Depends(get_db),
):
    topic = _load(db, topic_id)
    svc.set_active(db, caller.user, topic, body.active, ip=caller.ip)
    db.commit()
    return _detail(topic)
