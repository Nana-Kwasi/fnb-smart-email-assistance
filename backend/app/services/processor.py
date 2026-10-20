"""What happens to each customer email, from arrival to reply.

  1. Store it (once: the Outlook message id is unique, so a retry never duplicates it).
  2. Skip it if it is not from a customer (auto-replies, bounces, our own mailbox).
  3. Mark it high priority if it mentions a sensitive keyword (fraud, complaint, ...).
  4. Send the acknowledgement (template set in Settings). A later email in a thread that was
     already acknowledged in the last week does not get a second one.
  5. Check attachments (type, size, malware scan) and classify the email (rules, plus the AI
     model when switched on), and link it to a case.
  6. Recognise the topic. Approved text (knowledge base, bank data) is preferred; otherwise the
     AI drafts an answer from approved sources and independent checks test it.
  7. The policy engine (services/policy.py) decides: send automatically, hand to a person (with
     the draft and the reason), or urgent escalation (with an immediate alert).

Every send is attempted at most once. If the worker stops halfway, the email goes to the
"Needs attention" list rather than risk the customer getting the same email twice.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import as_utc, utcnow
from app.models import (
    AckStatus,
    AiDraft,
    CustomerEmail,
    DraftStatus,
    EmailReply,
    EmailStatus,
    HandledVia,
    KnowledgeEntry,
    Priority,
    Reason,
    ReplyKind,
    StaffUser,
    TopicRevision,
)
from app.services import attachments as attachment_service
from app.services import audit, cases, grounding, notifications
from app.services.answer_builder import build_answer, sender_is_verified, wrap_answer
from app.services.app_settings import get_app_settings, get_mailbox_state
from app.services.attachments import RawAttachment, Scanner
from app.services.classifier import classify
from app.services.datasources import DataSources
from app.services.email_text import (
    automation_check,
    clean_body,
    customer_greeting_name,
    fill,
    new_reference,
    strip_quoted_history,
    to_html,
)
from app.services.graph_client import GraphError, InboxMessage, SentItem
from app.services.knowledge import category_is_automatic
from app.services.llm import LanguageModel
from app.services.matcher import Matcher
from app.services.policy import Route, Signals, Verbatim, category_policy, decide, get_policy
from app.services.topics import build_matcher

log = logging.getLogger(__name__)

INTERRUPTED_AFTER = timedelta(minutes=10)


class Mailbox(Protocol):
    def inbox_changes(self, delta_link: str | None) -> tuple[list[str], str]: ...
    def get_message(self, message_id: str) -> InboxMessage: ...
    def reply(self, message_id: str, html: str) -> None: ...
    def send_mail(self, to: list[str], subject: str, html: str, cc: list[str] | None = None) -> None: ...
    def sent_in_conversation(self, conversation_id: str) -> list[SentItem]: ...
    def list_attachments(self, message_id: str, max_bytes: int) -> list[RawAttachment]: ...


def find_sensitive(text: str, keywords: list[str]) -> list[str]:
    """Keywords found as whole words or phrases ("court" must not match "courtesy")."""
    lowered = text.lower()
    return [k for k in keywords if k.strip() and re.search(rf"\b{re.escape(k.strip().lower())}\b", lowered)]


def send_reply(
    db: Session,
    mailbox: Mailbox,
    email: CustomerEmail,
    kind: str,
    text: str,
    *,
    revision_id=None,
    actor: StaffUser | None = None,
    request_id: str | None = None,
    draft_id=None,
) -> EmailReply:
    """Send one reply in the customer's thread and keep an exact record of it."""
    reply = EmailReply(
        email_id=email.id,
        kind=kind,
        body_text=text,
        revision_id=revision_id,
        sent_by_id=actor.id if actor else None,
        request_id=request_id,
        draft_id=draft_id,
    )
    db.add(reply)
    if request_id:
        db.flush()  # the unique request id is claimed before anything is sent
    try:
        mailbox.reply(email.graph_message_id, to_html(text))
        reply.succeeded = True
    except Exception as exc:  # noqa: BLE001 - any failure is recorded, never retried blindly
        log.error("Reply (%s) failed for %s: %s", kind, email.reference, exc)
        reply.error = str(exc)[:500]
    db.flush()
    return reply


def store_message(db: Session, msg: InboxMessage) -> CustomerEmail | None:
    if db.scalar(select(CustomerEmail.id).where(CustomerEmail.graph_message_id == msg.id)):
        return None
    follow_up = False
    if msg.conversation_id:
        follow_up = (
            db.scalar(
                select(CustomerEmail.id).where(
                    CustomerEmail.conversation_id == msg.conversation_id,
                    CustomerEmail.ack_status == AckStatus.SENT,
                    CustomerEmail.received_at
                    >= msg.received_at - timedelta(days=get_app_settings(db).follow_up_days),
                )
            )
            is not None
        )
    email = CustomerEmail(
        reference=new_reference(msg.received_at),
        graph_message_id=msg.id,
        conversation_id=msg.conversation_id,
        from_address=msg.from_address[:320],
        from_name=msg.from_name[:255],
        subject=msg.subject[:998],
        body_text=clean_body(msg.body_text),
        received_at=msg.received_at,
        sender_verified=sender_is_verified(msg.headers),
        web_link=msg.web_link,
        is_follow_up=follow_up,
        status=EmailStatus.RECEIVED,
    )
    db.add(email)
    db.commit()
    return email


def process_email(
    db: Session,
    mailbox: Mailbox,
    email: CustomerEmail,
    msg: InboxMessage,
    matcher: Matcher,
    sources: DataSources,
    settings: Settings,
    *,
    model: LanguageModel | None = None,
    scanner: Scanner | None = None,
) -> None:
    cfg = get_app_settings(db)
    rules = get_policy(db)
    email.processing_started_at = utcnow()
    db.commit()

    automation = automation_check(msg.from_address, msg.headers, settings.graph_mailbox)
    if automation and automation[1]:
        email.status, email.status_reason, email.reason_code = (
            EmailStatus.SKIPPED,
            automation[0],
            Reason.AUTOMATED,
        )
        email.ack_status = AckStatus.NOT_APPLICABLE
        audit.record(
            db, "email.skipped", entity_type="email", entity_id=email.id, detail={"reason": automation[0]}
        )
        db.commit()
        return
    may_auto_send = automation is None
    sender_domain = msg.from_address.lower().rsplit("@", 1)[-1]
    internal = sender_domain in settings.internal_domains and not rules.ack_internal_senders

    question = strip_quoted_history(email.body_text)
    sensitive = find_sensitive(f"{email.subject}\n{question}", list(cfg.sensitive_keywords or []))
    if sensitive:
        email.priority = Priority.HIGH

    # Attachments: checked and scanned before anything reads them.
    attachment_problem, attachments_text = None, ""
    if msg.has_attachments:
        max_bytes = settings.attachment_max_mb * 1024 * 1024
        try:
            raw = mailbox.list_attachments(msg.id, max_bytes)
        except Exception as exc:  # noqa: BLE001 - the email still goes on, to a person
            log.warning("Reading attachments failed for %s: %s", email.reference, type(exc).__name__)
            raw, attachment_problem = [], "Attachments could not be read from Outlook"
        rows, problem = attachment_service.process(raw, scanner, max_bytes)
        email.attachments.extend(rows)
        attachment_problem = attachment_problem or problem
        attachments_text = "\n\n".join(r.extracted_text for r in rows if r.extracted_text)

    # Classification: rules always, the model when it is switched on.
    c = classify(db, model, email.subject, question, attachments_text=attachments_text, email_id=email.id)
    cat = category_policy(rules, c.category)
    email.category, email.intent, email.language = c.category, c.intent, c.language
    email.urgency, email.risk, email.sentiment = c.urgency, c.risk, c.sentiment
    email.department = c.department or (cat or {}).get("department")
    email.ai_summary, email.classification, email.classified_by = c.summary, c.as_json(), c.classified_by
    if c.urgency in ("high", "critical") or (cat and cat.get("route") == "urgent"):
        email.priority = Priority.HIGH
    db.commit()

    case = cases.attach(
        db,
        email,
        category=c.category,
        department=email.department,
        priority=email.priority,
        sla_hours=(cat or {}).get("sla_hours") or 24,
    )
    cases.add_event(
        db,
        case,
        "classified",
        email=email,
        detail={
            "category": c.category,
            "urgency": c.urgency,
            "risk": c.risk,
            "language": c.language,
            "by": c.classified_by,
            "model_error": c.model_error,
        },
    )
    db.commit()

    # Approved-text answers (knowledge base or bank data), as before.
    match = matcher.match(email.subject, question, min(cfg.suggest_threshold, cfg.confidence_threshold))
    if match.ranking:
        email.match_score = match.ranking[0].score
    if match.matched and match.matched.kind == "knowledge":
        email.knowledge_entry_id, email.knowledge_version_id = (
            match.matched.topic_id,
            match.matched.revision_id,
        )
    elif match.matched:
        email.match_topic_id, email.match_revision_id = match.matched.topic_id, match.matched.revision_id

    ack_wanted = may_auto_send and not email.is_follow_up and not internal and (cat or {}).get("ack", True)
    if not ack_wanted:
        email.ack_status = AckStatus.NOT_APPLICABLE
    elif not cfg.ack_enabled:
        email.ack_status = AckStatus.DISABLED
    else:
        email.ack_attempts, email.ack_last_attempt_at = 1, utcnow()
        db.commit()  # the attempt is on record before anything is sent
        ack = send_reply(db, mailbox, email, ReplyKind.ACKNOWLEDGEMENT, acknowledgement_text(cfg, email))
        email.ack_status = AckStatus.SENT if ack.succeeded else AckStatus.FAILED
        cases.add_event(db, case, "acknowledged" if ack.succeeded else "acknowledgement_failed", email=email)
    db.commit()

    signals = Signals(
        may_auto_send=may_auto_send,
        automation_reason=automation[0] if automation else None,
        internal_sender=internal,
        sensitive=sensitive,
        classification=c,
        attachment_problem=attachment_problem,
        ack_failed=email.ack_status == AckStatus.FAILED,
        verbatim=_cached(lambda: _verbatim(db, email, match, cfg, sources)),
    )
    decision = decide(db, signals, cfg)

    # An AI draft, for officers (or, in an autonomous mode, to send), when approved text did not answer.
    draft: AiDraft | None = None
    if (
        decision.send != "verbatim"
        and decision.route != Route.URGENT
        and model is not None
        and model.enabled
        and c.model_error is None  # the model just failed: do not wait for it a second time
        and c.category != "spam"
        and not c.injection
        and not internal
    ):
        grounded = grounding.draft_answer(
            db,
            model,
            email.subject,
            question,
            min_evidence=float((cat or {}).get("min_evidence", 0.5)),
            max_chars=cfg.max_auto_answer_chars,
            email_id=email.id,
        )
        signals.ai = grounded
        if grounded.answer is not None:
            draft = AiDraft(
                email_id=email.id,
                body_text=wrap_answer(cfg, email, grounded.answer),
                citations=[
                    {
                        "n": e.n,
                        "entry_id": e.entry_id,
                        "version_id": e.version_id,
                        "document": e.document,
                        "document_id": e.document_id,
                        "version": e.version,
                        "title": e.title,
                        "score": round(e.score, 3),
                    }
                    for e in grounded.cited
                ],
                validation=[
                    {"check": k.check, "passed": k.passed, "detail": k.detail} for k in grounded.checks
                ],
                passed=grounded.passed,
                model=grounded.model or "",
                policy_version=rules.version,
            )
            db.add(draft)
            db.flush()
            cases.add_event(
                db,
                case,
                "draft_created",
                email=email,
                detail={"passed": grounded.passed, "model": grounded.model},
            )
        db.commit()
        decision = decide(db, signals, cfg)

    email.decision = decision.as_json(rules)
    blocked: tuple[str, str] | None = None
    if decision.route == Route.AUTO:
        if decision.send == "verbatim":
            v = signals.verbatim()
            sent = send_reply(db, mailbox, email, ReplyKind.AUTO_ANSWER, v.text, revision_id=v.revision_id)
            how = v.how
        else:
            sent = send_reply(db, mailbox, email, ReplyKind.AI_ANSWER, draft.body_text)
            sent.draft_id = draft.id
            if sent.succeeded:
                draft.status = DraftStatus.SENT
            how = f"Answered by the assistant ({draft.model}) from approved sources."
        if sent.succeeded:
            email.status, email.handled_via, email.handled_at = (
                EmailStatus.AUTO_ANSWERED,
                HandledVia.AUTOMATIC,
                utcnow(),
            )
            email.status_reason = how[:255]
            cases.add_event(db, case, "answered_automatically", email=email, detail={"how": how[:255]})
        else:
            blocked = ("Outlook did not send the automatic answer", Reason.SEND_FAILED)
    else:
        blocked = (decision.reason, decision.reason_code or Reason.NO_MATCH)
    if blocked is not None:
        email.status, email.status_reason, email.reason_code = (
            EmailStatus.QUEUED,
            blocked[0][:255],
            blocked[1],
        )
        if decision.route == Route.URGENT:
            email.priority = case.priority = Priority.HIGH
            case.handoffs = (case.handoffs or 0) + 1
            cases.add_event(
                db,
                case,
                "escalated_urgent",
                email=email,
                detail={"reason": blocked[0][:255], "department": email.department},
            )
        else:
            cases.add_event(db, case, "sent_to_officer", email=email, detail={"reason": blocked[0][:255]})

    audit.record(
        db,
        "email.processed",
        entity_type="email",
        entity_id=email.id,
        detail={
            "reference": email.reference,
            "status": email.status,
            "ack": email.ack_status,
            "score": email.match_score,
            "reason": email.status_reason,
            "route": decision.route,
            "category": c.category,
            "classified_by": c.classified_by,
            "policy_version": rules.version,
            "mode": rules.mode,
            "draft_model": draft.model if draft else None,
            "sources": [x["version_id"] for x in draft.citations] if draft else [],
        },
    )
    cases.sync(db, case)
    db.commit()

    if email.status == EmailStatus.QUEUED and email.priority == Priority.HIGH:
        notifications.alert_urgent(db, mailbox, email, settings)


def acknowledgement_text(cfg, email: CustomerEmail) -> str:
    return fill(
        cfg.ack_template, customer_name=customer_greeting_name(email.from_name), reference=email.reference
    )


def _cached(fn):
    box: list = []

    def get():
        if not box:
            box.append(fn())
        return box[0]

    return get


def _verbatim(db: Session, email: CustomerEmail, match, cfg, sources: DataSources) -> Verbatim:
    """The approved-text answer, or why it cannot be sent by itself."""
    if match.matched is None:
        return Verbatim(blocked=(match.reason, match.reason_code or Reason.NO_MATCH))
    if not cfg.auto_reply_enabled:
        return Verbatim(
            blocked=(
                f"Automatic answers are switched off; suggested answer: {match.matched.title}",
                Reason.AUTO_OFF,
            )
        )
    if match.matched.score < cfg.confidence_threshold:
        return Verbatim(
            blocked=(
                f"Possible answer “{match.matched.title}” is {match.matched.score:.0%} sure; "
                f"{cfg.confidence_threshold:.0%} is needed to send it",
                Reason.BELOW_AUTO,
            )
        )
    if match.matched.kind == "knowledge":
        entry = db.get(KnowledgeEntry, match.matched.topic_id)
        document = entry.version.document
        if not category_is_automatic(cfg, document.category):
            return Verbatim(
                blocked=(
                    f"Answer found in “{document.name}”; "
                    f"{document.category} answers need a person to send them",
                    Reason.SUGGEST_ONLY,
                )
            )
        if len(entry.content) > cfg.max_auto_answer_chars:
            return Verbatim(
                blocked=(
                    f"Answer found in “{document.name}”, but it is too long to send by itself",
                    Reason.ANSWER_TOO_LONG,
                )
            )
        return Verbatim(
            text=wrap_answer(cfg, email, entry.content),
            how=f"Answered from “{document.name}” (version {entry.version.version_label}).",
        )
    revision = db.get(TopicRevision, match.matched.revision_id)
    built = build_answer(db, revision, email, cfg, sources)
    if built.text is None:
        return Verbatim(blocked=(built.reason, built.reason_code))
    return Verbatim(text=built.text, revision_id=revision.id, how=f"{match.reason} {built.reason}.")


def _queue_for_officer(email: CustomerEmail, reason: str) -> None:
    email.status, email.reason_code, email.status_reason = EmailStatus.QUEUED, Reason.INTERRUPTED, reason


def _stored_but_not_started(db: Session) -> list:
    return list(
        db.scalars(
            select(CustomerEmail.id).where(
                CustomerEmail.status == EmailStatus.RECEIVED, CustomerEmail.processing_started_at.is_(None)
            )
        )
    )


def _process_safely(db, mailbox, email, msg, matcher, sources, settings, **ai) -> None:
    """Process one email; if it fails part-way, hand it to a person instead of leaving it stuck.

    Something may already have been sent before the failure, so it is never retried
    automatically.
    """
    try:
        process_email(db, mailbox, email, msg, matcher, sources, settings, **ai)
    except Exception as exc:  # noqa: BLE001 - one bad email must not block the others
        db.rollback()
        log.error("Processing %s failed: %s", email.reference, type(exc).__name__)
        email = db.get(CustomerEmail, email.id)
        if email is not None and email.status == EmailStatus.RECEIVED:
            _queue_for_officer(email, "Processing failed part-way; check Outlook before replying")
            audit.record(
                db,
                "email.processing_failed",
                entity_type="email",
                entity_id=email.id,
                detail={"reference": email.reference, "error": type(exc).__name__},
            )
            db.commit()


ACK_MAX_ATTEMPTS = 3
ACK_RETRY_AFTER = timedelta(minutes=2)  # doubled after each attempt
_SIGNATURE_CHARS = 60


def _signature(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())[:_SIGNATURE_CHARS]


def retry_acknowledgements(db: Session, mailbox: Mailbox) -> int:
    """Try failed acknowledgements again, with growing waits, at most ACK_MAX_ATTEMPTS times.

    A send that reported an error may still have gone out, so Sent Items is checked first:
    if the acknowledgement is there, it is recorded as sent instead of being sent again.
    """
    cfg = get_app_settings(db)
    if not cfg.ack_enabled:
        return 0
    now = utcnow()
    candidates = db.scalars(
        select(CustomerEmail).where(
            CustomerEmail.ack_status.in_((AckStatus.FAILED, AckStatus.PENDING)),
            CustomerEmail.ack_attempts >= 1,
            CustomerEmail.ack_attempts < ACK_MAX_ATTEMPTS,
            CustomerEmail.status != EmailStatus.RECEIVED,
        )
    ).unique()
    sent = 0
    for email in candidates:
        wait = ACK_RETRY_AFTER * (2 ** (email.ack_attempts - 1))
        if email.ack_last_attempt_at and now - as_utc(email.ack_last_attempt_at) < wait:
            continue
        text = acknowledgement_text(cfg, email)
        if email.conversation_id:
            try:
                already = any(
                    _signature(item.preview) == _signature(text)
                    for item in mailbox.sent_in_conversation(email.conversation_id)
                )
            except Exception as exc:  # noqa: BLE001 - cannot tell; try again next cycle
                log.warning("Could not check Sent Items for %s: %s", email.reference, type(exc).__name__)
                continue
            if already:
                email.ack_status = AckStatus.SENT
                db.commit()
                continue
        email.ack_attempts += 1
        email.ack_last_attempt_at = now
        db.commit()
        reply = send_reply(db, mailbox, email, ReplyKind.ACKNOWLEDGEMENT, text)
        email.ack_status = AckStatus.SENT if reply.succeeded else AckStatus.FAILED
        audit.record(
            db,
            "email.acknowledgement_retried",
            entity_type="email",
            entity_id=email.id,
            detail={"reference": email.reference, "attempt": email.ack_attempts, "ok": reply.succeeded},
        )
        db.commit()
        sent += int(reply.succeeded)
    return sent


def recover_interrupted(db: Session) -> int:
    """Move emails left half-processed by a crash to "Needs attention" instead of resending."""
    cutoff = utcnow() - INTERRUPTED_AFTER
    stuck = [
        e
        for e in db.scalars(
            select(CustomerEmail).where(
                CustomerEmail.status == EmailStatus.RECEIVED, CustomerEmail.processing_started_at.is_not(None)
            )
        )
        if as_utc(e.processing_started_at) < cutoff
    ]
    for email in stuck:
        email.status, email.reason_code = EmailStatus.QUEUED, Reason.INTERRUPTED
        email.status_reason = "Processing was interrupted; check Outlook before replying"
    db.commit()
    return len(stuck)


def poll_once(
    db: Session,
    mailbox: Mailbox,
    sources: DataSources,
    settings: Settings,
    *,
    model: LanguageModel | None = None,
    scanner: Scanner | None = None,
) -> int:
    """One sync with the mailbox. Returns how many new emails were processed."""
    ai = {"model": model, "scanner": scanner}
    state = get_mailbox_state(db)
    processed = 0
    try:
        if state.delta_link is None:
            # First run: remember where the inbox is now and only process what arrives later,
            # so old emails never get a surprise reply.
            _, state.delta_link = mailbox.inbox_changes(None)
            state.process_from = utcnow()
        else:
            ids, next_link = mailbox.inbox_changes(state.delta_link)
            unstarted = _stored_but_not_started(db)
            matcher = build_matcher(db) if ids or unstarted else None
            for message_id in ids:
                try:
                    msg = mailbox.get_message(message_id)
                except GraphError as exc:
                    if exc.status == 404:
                        # Deleted or moved before we read it; nothing to answer.
                        log.warning("Message disappeared before it could be read; skipped")
                        continue
                    raise
                if state.process_from and msg.received_at < as_utc(state.process_from):
                    continue
                email = store_message(db, msg)
                if email is not None:
                    _process_safely(db, mailbox, email, msg, matcher, sources, settings, **ai)
                    processed += 1
            # Stored by an earlier cycle that failed before processing began. Nothing has been
            # sent for these yet, so it is safe to process them normally now.
            for email_id in unstarted:
                email = db.get(CustomerEmail, email_id)
                if email is None or email.status != EmailStatus.RECEIVED or email.processing_started_at:
                    continue
                try:
                    msg = mailbox.get_message(email.graph_message_id)
                except GraphError as exc:
                    if exc.status != 404:
                        raise
                    _queue_for_officer(email, "The email could no longer be read from Outlook")
                    db.commit()
                    continue
                _process_safely(db, mailbox, email, msg, matcher, sources, settings, **ai)
                processed += 1
            state = get_mailbox_state(db)
            state.delta_link = next_link
        state.last_poll_ok, state.last_error, state.consecutive_failures = True, None, 0
    except Exception as exc:  # noqa: BLE001 - recorded for the dashboard, retried next cycle
        db.rollback()
        state = get_mailbox_state(db)
        state.last_poll_ok, state.last_error = False, str(exc)[:500]
        state.consecutive_failures = (state.consecutive_failures or 0) + 1
        log.error("Mailbox sync failed: %s", exc)
    state.last_poll_at = utcnow()
    db.commit()
    return processed
