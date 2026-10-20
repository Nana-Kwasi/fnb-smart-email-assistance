"""Topics: proposing changes, approving them (maker-checker), and building the matcher."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.db import utcnow
from app.models import RevisionStatus, StaffUser, Topic, TopicRevision
from app.services import audit
from app.services.app_settings import get_app_settings
from app.services.datasources import (
    DataSources,
    QueryRejected,
    builder_extractors,
    builder_is_customer_specific,
    check_builder_against_table,
    query_is_customer_specific,
    template_fields,
    validate_builder,
    validate_extractors,
    validate_query,
)
from app.services.matcher import Candidate, Matcher


class TopicRuleError(Exception):
    """A request that breaks the topic rules. The message is safe to show."""


@dataclass
class TopicDraft:
    title: str
    examples: list[str]
    data_source: str
    reply_template: str
    builder: dict | None = None  # guided topic
    query: str | None = None  # advanced topic (hand-written SELECT)
    row_template: str | None = None
    extractors: dict[str, str] = field(default_factory=dict)
    max_rows: int = 10
    require_verified_sender: bool = True


def live_topics(db: Session) -> list[Topic]:
    return list(
        db.scalars(
            select(Topic)
            .options(selectinload(Topic.live_revision))
            .where(Topic.is_active.is_(True), Topic.live_revision_id.is_not(None))
        )
    )


def build_matcher(db: Session, *, include_draft_knowledge: bool = False) -> Matcher:
    """Everything the assistant can answer from: live topics and the live knowledge base."""
    from app.services.knowledge import knowledge_candidates

    topics = [
        Candidate(t.id, t.live_revision.id, t.live_revision.title, list(t.live_revision.examples))
        for t in live_topics(db)
    ]
    return Matcher(topics + knowledge_candidates(db, include_drafts=include_draft_knowledge))


def _clean_examples(examples: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for ex in examples:
        ex = " ".join(ex.split())
        if ex and ex.lower() not in seen:
            seen.add(ex.lower())
            out.append(ex)
    return out


BUILTIN_FIELDS = {"customer_name", "reference", "rows"}
CUSTOMER_DATA_OFF = (
    "Answers from a customer's own records are not enabled yet (phase 1). "
    "Topics can only give the same answer to everyone."
)


def validate_draft(db: Session, draft: TopicDraft, sources: DataSources) -> TopicDraft:
    draft.examples = _clean_examples(draft.examples)
    if not draft.examples:
        raise TopicRuleError("Add at least one example of how a customer might ask this.")
    customer_ok = get_settings().customer_data_answers
    try:
        ds = sources.get(db, draft.data_source)
        if draft.builder is not None:
            draft.builder = validate_builder(draft.builder)
            if builder_is_customer_specific(draft.builder) and not customer_ok:
                raise TopicRuleError(CUSTOMER_DATA_OFF)
            try:
                table = sources.table(ds, draft.builder["table"])
            except QueryRejected:
                raise
            except Exception as exc:  # noqa: BLE001 - database unreachable
                raise TopicRuleError(
                    f"Could not read '{ds.name}' to check the table ({type(exc).__name__})."
                ) from exc
            check_builder_against_table(draft.builder, table)
            draft.query, draft.extractors = None, builder_extractors(draft.builder)
            if builder_is_customer_specific(draft.builder):
                draft.require_verified_sender = True  # customer data only goes to verified senders
            known = {c.lower() for c in draft.builder["columns"]} | BUILTIN_FIELDS
            for template in (draft.reply_template, draft.row_template or ""):
                unknown = [f for f in template_fields(template) if f.lower() not in known]
                if unknown:
                    raise TopicRuleError(
                        "The reply uses "
                        + ", ".join(f"{{{f}}}" for f in unknown)
                        + ", which is not one of the chosen columns."
                    )
            if "{rows}" in draft.reply_template and not draft.builder["many"]:
                raise TopicRuleError("{rows} needs the topic to reply with a list of records.")
        else:
            if not (draft.query or "").strip():
                raise TopicRuleError("Choose a table, or write a query in Advanced mode.")
            validate_extractors(draft.extractors)
            draft.query = validate_query(draft.query, list(draft.extractors))
            if query_is_customer_specific(draft.query, draft.extractors) and not customer_ok:
                raise TopicRuleError(CUSTOMER_DATA_OFF)
    except QueryRejected as exc:
        raise TopicRuleError(str(exc)) from exc
    if "{rows}" in draft.reply_template and not (draft.row_template or "").strip():
        raise TopicRuleError("The reply uses {rows}, so a layout for each row is needed.")
    return draft


def propose(
    db: Session,
    actor: StaffUser,
    draft: TopicDraft,
    sources: DataSources,
    *,
    topic: Topic | None = None,
    ip: str | None = None,
) -> TopicRevision:
    """Create a topic, or a change to one. Nothing goes live until someone else approves it."""
    draft = validate_draft(db, draft, sources)
    if topic is None:
        topic = Topic()
        db.add(topic)
        db.flush()
    # Only one change waits for approval at a time; a newer edit replaces an unreviewed one.
    for rev in topic.revisions:
        if rev.status == RevisionStatus.PENDING:
            rev.status = RevisionStatus.SUPERSEDED
    revision = TopicRevision(
        topic_id=topic.id,
        title=draft.title.strip(),
        examples=draft.examples,
        data_source=draft.data_source,
        builder=draft.builder,
        query=draft.query,
        extractors=dict(draft.extractors),
        max_rows=draft.max_rows,
        reply_template=draft.reply_template.strip(),
        row_template=(draft.row_template or "").strip() or None,
        require_verified_sender=draft.require_verified_sender,
        submitted_by_id=actor.id,
        status=RevisionStatus.PENDING,
    )
    db.add(revision)
    db.flush()
    audit.record(
        db,
        "topic.proposed",
        actor=actor,
        entity_type="topic",
        entity_id=topic.id,
        detail={"revision_id": str(revision.id), "title": revision.title},
        ip=ip,
    )
    return revision


def _pending(db: Session, topic: Topic, revision_id) -> TopicRevision:
    revision = db.get(TopicRevision, revision_id)
    if revision is None or revision.topic_id != topic.id:
        raise TopicRuleError("That change does not belong to this topic.")
    if revision.status != RevisionStatus.PENDING:
        raise TopicRuleError("This change has already been reviewed.")
    return revision


def approve(db: Session, actor: StaffUser, topic: Topic, revision_id, ip: str | None = None) -> TopicRevision:
    revision = _pending(db, topic, revision_id)
    if revision.submitted_by_id == actor.id and get_app_settings(db).require_second_person:
        raise TopicRuleError(
            "You cannot approve your own change; someone else must (Settings: second-person check)."
        )
    if topic.live_revision is not None:
        topic.live_revision.status = RevisionStatus.SUPERSEDED
    revision.status = RevisionStatus.APPROVED
    revision.reviewed_by_id = actor.id
    revision.reviewed_at = utcnow()
    topic.live_revision = revision  # keeps the id and the loaded relationship in step
    audit.record(
        db,
        "topic.approved",
        actor=actor,
        entity_type="topic",
        entity_id=topic.id,
        detail={"revision_id": str(revision.id), "title": revision.title},
        ip=ip,
    )
    return revision


def reject(
    db: Session, actor: StaffUser, topic: Topic, revision_id, note: str, ip: str | None = None
) -> TopicRevision:
    revision = _pending(db, topic, revision_id)
    revision.status = RevisionStatus.REJECTED
    revision.reviewed_by_id = actor.id
    revision.reviewed_at = utcnow()
    revision.review_note = note.strip()[:500] or None
    audit.record(
        db,
        "topic.rejected",
        actor=actor,
        entity_type="topic",
        entity_id=topic.id,
        detail={"revision_id": str(revision.id), "note": revision.review_note},
        ip=ip,
    )
    return revision


def set_active(db: Session, actor: StaffUser, topic: Topic, active: bool, ip: str | None = None) -> None:
    topic.is_active = active
    audit.record(
        db,
        "topic.activated" if active else "topic.deactivated",
        actor=actor,
        entity_type="topic",
        entity_id=topic.id,
        ip=ip,
    )
