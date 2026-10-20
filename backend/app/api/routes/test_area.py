"""Try an email against the knowledge base and live topics without sending anything."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import Caller, require
from app.api.services import get_sources
from app.core.db import get_db
from app.models import CustomerEmail, KnowledgeEntry, TopicRevision
from app.schemas import ScoredOut, TestMatchIn, TestMatchOut
from app.security.rbac import Permission, has_permission
from app.services import audit
from app.services.answer_builder import build_answer, wrap_answer
from app.services.app_settings import get_app_settings
from app.services.datasources import DataSources
from app.services.email_text import customer_greeting_name, fill, strip_quoted_history
from app.services.knowledge import category_is_automatic
from app.services.processor import find_sensitive
from app.services.topics import build_matcher

router = APIRouter(prefix="/test", tags=["test area"])


@router.post("/match", response_model=TestMatchOut)
def test_match(
    body: TestMatchIn,
    caller: Caller = Depends(require(Permission.USE_TEST_AREA)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
) -> TestMatchOut:
    if body.run_query and not has_permission(caller.user.role, Permission.RUN_TEST_QUERIES):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can read real data in the test area.")

    cfg = get_app_settings(db)
    question = strip_quoted_history(body.body)
    sensitive = find_sensitive(f"{body.subject}\n{question}", list(cfg.sensitive_keywords or []))
    result = build_matcher(db).match(
        body.subject, question, min(cfg.suggest_threshold, cfg.confidence_threshold)
    )
    reference = "FNB-TEST-000000"
    name = customer_greeting_name(body.sender_name)

    answer_preview = answer_reason = None
    knowledge = result.matched is not None and result.matched.kind == "knowledge"
    if knowledge:
        entry = db.get(KnowledgeEntry, result.matched.topic_id)
        doc = entry.version.document
        sample = CustomerEmail(
            reference=reference, from_name=body.sender_name, from_address=body.sender_email
        )
        answer_preview = wrap_answer(cfg, sample, entry.content)
        answer_reason = f"From “{doc.name}” (version {entry.version.version_label})"
    if result.matched and body.run_query and not knowledge:
        # A throwaway email object, never saved, so the answer is built exactly as in production.
        email = CustomerEmail(
            reference=reference,
            from_address=body.sender_email,
            from_name=body.sender_name,
            subject=body.subject,
            body_text=body.body,
            received_at=datetime.now(UTC),
            sender_verified=body.verified,
        )
        built = build_answer(db, db.get(TopicRevision, result.matched.revision_id), email, cfg, sources)
        answer_preview, answer_reason = built.text, built.reason
        audit.record(
            db,
            "test.query_run",
            actor=caller.user,
            entity_type="topic",
            entity_id=result.matched.topic_id,
            detail={"sender_email": body.sender_email, "found": built.text is not None},
            ip=caller.ip,
        )
        db.commit()

    if sensitive:
        decision = "Needs a person (urgent): mentions " + ", ".join(sensitive)
    elif not result.matched:
        decision = "Needs a person: " + result.reason
    elif not cfg.auto_reply_enabled:
        decision = f"Needs a person, with “{result.matched.title}” suggested (automatic answers are off)"
    elif result.matched.score < cfg.confidence_threshold:
        decision = (
            f"Needs a person, with “{result.matched.title}” suggested "
            f"({result.matched.score:.0%} sure; {cfg.confidence_threshold:.0%} needed to send by itself)"
        )
    elif knowledge and not category_is_automatic(cfg, doc.category):
        decision = f"Needs a person, with the answer suggested ({doc.category} is set to “suggest only”)"
    elif knowledge and len(entry.content) > cfg.max_auto_answer_chars:
        decision = "Needs a person, with the answer suggested (too long to send by itself)"
    elif knowledge:
        decision = f"Automatic answer from the knowledge base: “{result.matched.title}”"
    elif body.run_query and answer_preview is None:
        decision = "Needs a person: " + (answer_reason or "no answer could be built")
    elif body.run_query:
        decision = f"Automatic answer from topic '{result.matched.title}'"
    else:
        decision = f"Automatic answer from topic '{result.matched.title}', if the database returns data"

    def scored(s) -> ScoredOut:
        return ScoredOut(
            topic_id=s.topic_id,
            title=s.title,
            score=s.score,
            best_example=s.best_example,
            best_sentence=s.best_sentence,
        )

    return TestMatchOut(
        would_auto_answer=decision.startswith("Automatic"),
        decision=decision,
        sensitive_keywords=sensitive,
        matched=scored(result.matched) if result.matched else None,
        ranking=[scored(s) for s in result.ranking[:5]],
        acknowledgement_preview=fill(cfg.ack_template, customer_name=name, reference=reference),
        answer_preview=answer_preview,
        answer_reason=answer_reason,
    )
