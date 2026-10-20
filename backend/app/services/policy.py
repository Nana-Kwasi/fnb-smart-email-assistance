"""The response policy engine: the one place that decides what happens to an email.

Three outcomes:
  * auto      - send an answer without a person (approved text, or an AI draft that passed
                every check in a mode and category that allow it)
  * review    - a person handles it, with the reason, and a draft when there is one
  * urgent    - restricted urgent handling (suspected fraud, account takeover): high priority,
                immediate alert, nothing automatic

Rules are checked in a fixed order; the first one that blocks wins, and every rule's result is
kept for the audit trail. The model's own confidence is never a reason to send: sending needs
category permission, retrieval quality, the independent validation checks and the mode.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.models import (
    AppSettings,
    AutomationPolicy,
    CustomerEmail,
    EmailReply,
    Mode,
    PolicyChange,
    Reason,
    ReplyKind,
    StaffUser,
)
from app.models.ai import DEFAULT_CATEGORY_POLICIES
from app.services import audit
from app.services.classifier import Classification
from app.services.grounding import GroundedDraft


class Route:
    AUTO = "auto"
    REVIEW = "review"
    URGENT = "urgent"


# -- the policy row -----------------------------------------------------------------------------


def get_policy(db: Session) -> AutomationPolicy:
    row = db.get(AutomationPolicy, 1)
    if row is None:
        row = AutomationPolicy(id=1)
        db.add(row)
        db.flush()
    return row


def category_policy(policy: AutomationPolicy, code: str | None) -> dict[str, Any] | None:
    if not code:
        return None
    for c in policy.categories or []:
        if c.get("code") == code:
            return {**next((d for d in DEFAULT_CATEGORY_POLICIES if d["code"] == code), {}), **c}
    return None


def change_policy(
    db: Session,
    changes: dict[str, Any],
    *,
    reason: str,
    actor: StaffUser | None = None,
    ip: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Apply changes, bump the version, and keep before/after with who and why."""
    policy = get_policy(db)
    diff = {}
    for key, value in changes.items():
        before = getattr(policy, key)
        if before != value:
            diff[key] = {"before": before, "after": value}
            setattr(policy, key, value)
    if not diff:
        return diff
    policy.version = (policy.version or 1) + 1
    policy.updated_by_id, policy.updated_at = (actor.id if actor else None), utcnow()
    db.add(
        PolicyChange(
            actor_id=actor.id if actor else None,
            actor_username=actor.username if actor else "system",
            version=policy.version,
            changes=diff,
            reason=reason[:500],
        )
    )
    audit.record(
        db,
        "policy.changed",
        actor=actor,
        entity_type="policy",
        entity_id=policy.version,
        detail={"fields": sorted(diff), "reason": reason[:200]},
        ip=ip,
    )
    return diff


# -- deciding -------------------------------------------------------------------------------------


@dataclass
class Verbatim:
    """The approved-text answer (knowledge base entry or bank-data topic), when there is one."""

    text: str | None = None
    revision_id: Any = None
    how: str = ""
    blocked: tuple[str, str] | None = None  # (reason, code) when it cannot be sent by itself


@dataclass
class Signals:
    may_auto_send: bool
    automation_reason: str | None
    internal_sender: bool
    sensitive: list[str]
    classification: Classification | None
    attachment_problem: str | None
    ack_failed: bool
    verbatim: Callable[[], Verbatim]  # worked out only when the earlier rules allow sending
    ai: GroundedDraft | None = None


@dataclass
class Decision:
    route: str
    reason: str
    reason_code: str | None
    send: str | None = None  # "verbatim" | "ai" when route is auto
    rules: list[dict] = field(default_factory=list)

    def as_json(self, policy: AutomationPolicy) -> dict:
        return {
            "route": self.route,
            "send": self.send,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "rules": self.rules,
            "mode": policy.mode,
            "policy_version": policy.version,
        }


def _ai_sent_today(db: Session, category: str) -> int:
    zone = ZoneInfo(get_settings().timezone)
    start = datetime.combine(datetime.now(zone).date(), time.min, tzinfo=zone)
    return (
        db.scalar(
            select(func.count())
            .select_from(EmailReply)
            .join(CustomerEmail, CustomerEmail.id == EmailReply.email_id)
            .where(
                EmailReply.kind == ReplyKind.AI_ANSWER,
                EmailReply.succeeded.is_(True),
                EmailReply.created_at >= start,
                CustomerEmail.category == category,
            )
        )
        or 0
    )


def decide(db: Session, s: Signals, cfg: AppSettings) -> Decision:
    policy = get_policy(db)
    c = s.classification
    cat = category_policy(policy, c.category if c else None)
    rules: list[dict] = []
    blocked: tuple[str, str, str] | None = None  # (route, reason, code)

    def rule(name: str, ok: bool, detail: str, route: str = Route.REVIEW, code: str | None = None) -> None:
        nonlocal blocked
        rules.append({"rule": name, "ok": ok, "detail": detail})
        if not ok and blocked is None:
            blocked = (route, detail, code or Reason.POLICY)

    urgent = bool(c and ((cat and cat.get("route") == "urgent") or c.urgency == "critical"))
    rule(
        "not_urgent",
        not urgent,
        "Possible fraud or security issue: "
        + ", ".join((c.urgent_signals if c else []) or [c.category or ""])
        if urgent
        else "no urgent signals",
        Route.URGENT,
        Reason.URGENT,
    )
    rule(
        "auto_reply_allowed",
        s.may_auto_send,
        s.automation_reason or "sender accepts automatic replies",
        code=Reason.NO_AUTO_REPLY,
    )
    rule(
        "customer_sender",
        not s.internal_sender,
        "Sent from inside the bank" if s.internal_sender else "external sender",
        code=Reason.INTERNAL_SENDER,
    )
    rule(
        "no_sensitive_words",
        not s.sensitive,
        "Sensitive topic: " + ", ".join(s.sensitive) if s.sensitive else "none found",
        code=Reason.SENSITIVE,
    )
    injection = c.injection if c else []
    rule(
        "no_prompt_injection",
        not injection,
        "The email tries to instruct the assistant" if injection else "none found",
        code=Reason.PROMPT_INJECTION,
    )
    rule(
        "attachments_ok",
        s.attachment_problem is None,
        s.attachment_problem or "no attachment issues",
        code=Reason.ATTACHMENT,
    )
    language = c.language if c else "en"
    allowed_languages = policy.auto_languages or ["en"]
    rule(
        "language_approved",
        language in allowed_languages,
        f"Written in {language}; automatic answers are approved for {', '.join(allowed_languages)}",
        code=Reason.LANGUAGE,
    )
    rule(
        "kill_switch_off",
        not policy.kill_switch,
        "Automatic answers are stopped (emergency switch)"
        + (f": {policy.kill_switch_reason}" if policy.kill_switch_reason else "")
        if policy.kill_switch
        else "off",
        code=Reason.KILL_SWITCH,
    )
    if cat is not None:
        rule(
            "category_not_paused",
            not cat.get("paused"),
            f"Automatic answers for {cat['label']} are paused"
            + (f": {cat['paused_reason']}" if cat.get("paused_reason") else "")
            if cat.get("paused")
            else "active",
        )
        rule(
            "category_allows_auto",
            bool(cat.get("auto_send")),
            f"{cat['label']} always needs a person"
            if not cat.get("auto_send")
            else f"{cat['label']} may be automatic",
        )
        if c and (c.needs_identity_verification or c.needs_staff_action):
            rule(
                "no_staff_action",
                False,
                "Needs identity verification" if c.needs_identity_verification else "Needs action by staff",
            )
    rule(
        "acknowledgement_ok",
        not s.ack_failed,
        "The acknowledgement failed to send" if s.ack_failed else "ok",
        code=Reason.SEND_FAILED,
    )

    if blocked is not None:
        route, reason, code = blocked
        return Decision(route, reason, code, rules=rules)

    # Approved text (knowledge base entry or bank-data topic) comes first.
    v = s.verbatim()
    if v.text is not None:
        rules.append({"rule": "approved_text", "ok": True, "detail": v.how})
        return Decision(Route.AUTO, v.how, None, send="verbatim", rules=rules)
    rules.append({"rule": "approved_text", "ok": False, "detail": v.blocked[0] if v.blocked else "none"})

    # An AI-written answer, only in an autonomous mode and with every check passed.
    ai = s.ai
    if ai is not None and ai.answer is not None:
        failed = [chk for chk in ai.checks if not chk.passed]
        rules.append(
            {
                "rule": "ai_checks",
                "ok": not failed,
                "detail": "all checks passed" if not failed else f"{failed[0].check}: {failed[0].detail}",
            }
        )
        if failed:
            return Decision(
                Route.REVIEW,
                f"Draft failed a safety check ({failed[0].check}): {failed[0].detail}",
                Reason.VALIDATION_FAILED,
                rules=rules,
            )
        why_not = None
        if policy.mode not in Mode.AUTONOMOUS:
            why_not = f"{Mode.LABELS[policy.mode]} mode: an officer checks every AI draft"
        elif cat is None:
            why_not = "The email could not be classified"
        elif cat.get("approval") == "supervisor":
            why_not = f"{cat['label']} drafts need a supervisor"
        elif c is None or c.risk != "low":
            why_not = f"Risk assessed as {c.risk if c else 'unknown'}"
        elif policy.mode == Mode.RESTRICTED and cat.get("risk_tier") != "low":
            why_not = (
                f"Restricted mode only answers low-risk categories ({cat['label']} is {cat.get('risk_tier')})"
            )
        elif policy.mode == Mode.EXPANDED and cat.get("risk_tier") == "high":
            why_not = f"{cat['label']} is a high-risk category"
        elif not cfg.auto_reply_enabled:
            why_not = "Automatic answers are switched off in Settings"
        elif (
            policy.mode == Mode.RESTRICTED and _ai_sent_today(db, cat["code"]) >= policy.restricted_daily_cap
        ):
            why_not = (
                f"Today's limit of {policy.restricted_daily_cap} automatic AI answers "
                f"for {cat['label']} reached"
            )
        rules.append(
            {"rule": "ai_may_send", "ok": why_not is None, "detail": why_not or "mode and category allow it"}
        )
        if why_not is None:
            return Decision(
                Route.AUTO, "Answered by the assistant from approved sources", None, send="ai", rules=rules
            )
        return Decision(Route.REVIEW, f"Draft ready to check. {why_not}", Reason.DRAFT_READY, rules=rules)

    if ai is not None and ai.error and c and c.model_error is None:
        rules.append({"rule": "ai_draft", "ok": False, "detail": ai.error})
    if c is not None and c.model_error:
        why = f"AI model unavailable ({c.model_error})"
        if v.blocked is not None:
            why += f"; {v.blocked[0]}"
        return Decision(Route.REVIEW, why, Reason.MODEL_UNAVAILABLE, rules=rules)
    if v.blocked is not None:
        return Decision(Route.REVIEW, v.blocked[0], v.blocked[1], rules=rules)
    if ai is not None and ai.error:
        return Decision(Route.REVIEW, ai.error, Reason.NO_MATCH, rules=rules)
    return Decision(Route.REVIEW, "Nothing to answer from", Reason.NO_MATCH, rules=rules)
