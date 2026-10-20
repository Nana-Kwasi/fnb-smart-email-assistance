"""Run an email through the whole decision pipeline without sending or storing anything.

Uses exactly the same pieces as real processing (rules, classifier, matcher, approved-text
answers, grounded drafting with validation, and the policy engine), so the Test area and the
evaluation script show what would really happen. Model statistics are not recorded.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import utcnow
from app.models import CustomerEmail
from app.services import grounding
from app.services.answer_builder import wrap_answer
from app.services.app_settings import get_app_settings
from app.services.classifier import Classification, classify
from app.services.datasources import DataSources
from app.services.email_text import new_reference, strip_quoted_history
from app.services.llm import LanguageModel
from app.services.policy import Decision, Route, Signals, category_policy, decide, get_policy
from app.services.processor import _cached, _verbatim, find_sensitive
from app.services.topics import build_matcher


@dataclass
class Simulation:
    classification: Classification
    decision: Decision
    approved_text: str | None
    draft: str | None
    draft_checks: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    draft_error: str | None = None


def simulate(
    db: Session,
    *,
    subject: str,
    body: str,
    sender: str = "customer@example.com",
    sender_name: str = "",
    sender_verified: bool = True,
    model: LanguageModel | None,
    sources: DataSources,
    settings: Settings,
    include_draft_knowledge: bool = False,
    run_queries: bool = False,
) -> Simulation:
    cfg = get_app_settings(db)
    rules = get_policy(db)
    email = CustomerEmail(
        id=uuid.uuid4(),
        reference=new_reference(utcnow()),
        graph_message_id="simulation",
        from_address=sender,
        from_name=sender_name,
        subject=subject,
        body_text=body,
        received_at=utcnow(),
        sender_verified=sender_verified,
    )
    question = strip_quoted_history(body)
    internal = (
        sender.lower().rsplit("@", 1)[-1] in settings.internal_domains and not rules.ack_internal_senders
    )
    sensitive = find_sensitive(f"{subject}\n{question}", list(cfg.sensitive_keywords or []))
    c = classify(None, model, subject, question)
    cat = category_policy(rules, c.category)
    matcher = build_matcher(db, include_draft_knowledge=include_draft_knowledge)
    match = matcher.match(subject, question, min(cfg.suggest_threshold, cfg.confidence_threshold))

    def verbatim():
        if not run_queries and match.matched is not None and match.matched.kind != "knowledge":
            from app.services.policy import Verbatim

            return Verbatim(
                blocked=(
                    f"Would read the answer for “{match.matched.title}” from the bank's data",
                    "simulated",
                )
            )
        return _verbatim(db, email, match, cfg, sources)

    signals = Signals(
        may_auto_send=True,
        automation_reason=None,
        internal_sender=internal,
        sensitive=sensitive,
        classification=c,
        attachment_problem=None,
        ack_failed=False,
        verbatim=_cached(verbatim),
    )
    decision = decide(db, signals, cfg)
    result = Simulation(classification=c, decision=decision, approved_text=None, draft=None)
    if decision.send == "verbatim":
        result.approved_text = signals.verbatim().text
        return result
    if (
        decision.route != Route.URGENT
        and model is not None
        and model.enabled
        and c.model_error is None
        and c.category != "spam"
        and not c.injection
        and not internal
    ):
        g = grounding.draft_answer(
            db,
            model,
            subject,
            question,
            min_evidence=float((cat or {}).get("min_evidence", 0.5)),
            max_chars=cfg.max_auto_answer_chars,
            include_drafts=include_draft_knowledge,
        )
        signals.ai = g
        result.evidence = [_evidence(e) for e in g.evidence]
        result.draft_error = g.error
        if g.answer is not None:
            result.draft = wrap_answer(cfg, email, g.answer)
            result.citations = [_evidence(e) for e in g.cited]
            result.draft_checks = [
                {"check": k.check, "passed": k.passed, "detail": k.detail} for k in g.checks
            ]
        result.decision = decide(db, signals, cfg)
    db.rollback()  # nothing from a simulation is kept
    return result


def _evidence(e: grounding.Evidence) -> dict[str, Any]:
    return {
        "n": e.n,
        "document": e.document,
        "version": e.version,
        "title": e.title,
        "score": round(e.score, 3),
    }
