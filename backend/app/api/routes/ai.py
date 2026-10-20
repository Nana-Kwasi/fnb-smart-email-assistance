"""AI & automation: model health and quality, the automation policy and its history, the kill
switch, and a dry run of the whole pipeline on a sample email.

Changing the policy needs the ai:manage permission and a written reason, and every change is
kept with before and after values. Moving to an autonomous mode also needs a recent evaluation
run with no unsafe automatic answers and no missed escalations.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Caller, require
from app.api.services import get_sources
from app.core.config import Settings, get_settings
from app.core.db import as_utc, get_db, utcnow
from app.models import CATEGORY_CODES, DEPARTMENTS, EvalRun, Mode, NotificationLog, PolicyChange
from app.security.rbac import Permission, has_permission
from app.services import audit, quality
from app.services.datasources import DataSources
from app.services.llm import get_model
from app.services.policy import change_policy, get_policy
from app.services.simulate import simulate

router = APIRouter(prefix="/ai", tags=["ai"])

EVAL_VALID_FOR = timedelta(days=30)
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)]


class CategoryPolicyIn(BaseModel):
    code: Literal[CATEGORY_CODES]  # type: ignore[valid-type]
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
    department: str
    ack: bool
    auto_send: bool
    approval: Literal["officer", "supervisor"]
    risk_tier: Literal["low", "medium", "high"]
    min_evidence: float = Field(ge=0.2, le=0.99)
    route: Literal["queue", "urgent"]
    sla_hours: int = Field(ge=1, le=720)
    max_send_attempts: int = Field(ge=1, le=5)
    paused: bool
    paused_reason: Annotated[str, StringConstraints(max_length=300)] | None = None


class PolicyIn(BaseModel):
    reason: Reason
    mode: Literal["draft_only", "supervised", "restricted", "expanded"] | None = None
    categories: list[CategoryPolicyIn] | None = Field(default=None, max_length=40)
    auto_languages: list[Literal["en", "tw", "mixed"]] | None = None
    ack_internal_senders: bool | None = None
    restricted_daily_cap: int | None = Field(default=None, ge=0, le=10_000)
    auto_pause_enabled: bool | None = None
    auto_pause_min_samples: int | None = Field(default=None, ge=3, le=1000)
    auto_pause_reopen_rate: float | None = Field(default=None, ge=0.01, le=1)


class KillSwitchIn(BaseModel):
    on: bool
    reason: Reason


class TryIn(BaseModel):
    subject: Annotated[str, StringConstraints(max_length=300)] = ""
    body: Annotated[str, StringConstraints(min_length=3, max_length=10_000)]
    sender: Annotated[str, StringConstraints(max_length=320)] = "customer@example.com"
    sender_verified: bool = True
    include_drafts: bool = True


def _policy_out(db: Session) -> dict[str, Any]:
    p = get_policy(db)
    return {
        "mode": p.mode,
        "mode_label": Mode.LABELS.get(p.mode, p.mode),
        "kill_switch": p.kill_switch,
        "kill_switch_reason": p.kill_switch_reason,
        "categories": p.categories,
        "auto_languages": p.auto_languages,
        "ack_internal_senders": p.ack_internal_senders,
        "restricted_daily_cap": p.restricted_daily_cap,
        "auto_pause_enabled": p.auto_pause_enabled,
        "auto_pause_min_samples": p.auto_pause_min_samples,
        "auto_pause_reopen_rate": p.auto_pause_reopen_rate,
        "version": p.version,
        "updated_at": p.updated_at,
        "departments": DEPARTMENTS,
        "modes": [{"value": m, "label": Mode.LABELS[m]} for m in Mode.ALL],
    }


def _model_health(settings: Settings) -> tuple[bool, str]:
    return get_model(settings).cached_health()


def _eval_ok(run: EvalRun | None, target: str | None = None) -> bool:
    """A clean run on the bank's own knowledge base, in the target mode (expanded covers restricted)."""
    m = (run.metrics or {}) if run else {}
    clean = bool(run) and m.get("unsafe_auto_send", 1) == 0 and m.get("missed_escalations", 1) == 0
    if target is None:
        return clean
    modes = {Mode.RESTRICTED: (Mode.RESTRICTED, Mode.EXPANDED), Mode.EXPANDED: (Mode.EXPANDED,)}[target]
    return clean and not m.get("isolated") and m.get("mode") in modes


@router.get("/status")
def ai_status(
    _: Caller = Depends(require(Permission.VIEW_AI)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    ok, detail = _model_health(settings) if settings.llm_enabled else (False, "Not switched on")
    runs = list(db.scalars(select(EvalRun).order_by(EvalRun.at.desc()).limit(10)))
    changes = list(db.scalars(select(PolicyChange).order_by(PolicyChange.at.desc()).limit(30)))
    incidents = list(
        db.scalars(
            select(NotificationLog)
            .where(NotificationLog.kind.in_(("system", "urgent")))
            .order_by(NotificationLog.sent_at.desc())
            .limit(15)
        )
    )
    out = {
        "model": {
            "enabled": settings.llm_enabled,
            "server": urlsplit(settings.llm_base_url).netloc or None,
            "fast_model": settings.llm_fast_model or None,
            "drafting_model": settings.llm_drafting_model or None,
            "healthy": ok,
            "health_detail": detail,
            "timeout_seconds": settings.llm_timeout_seconds,
            "max_concurrency": settings.llm_max_concurrency,
            "external_allowed": settings.llm_allow_external,
        },
        "attachment_scanning": bool(settings.clamd_host),
        "policy": _policy_out(db),
        "quality": quality.live_metrics(db, days=7),
        "evaluations": [
            {
                "id": r.id,
                "at": r.at,
                "dataset": r.dataset,
                "models": r.models,
                "cases": r.cases,
                "policy_version": r.policy_version,
                "metrics": r.metrics,
                "run_by": r.run_by,
                "ok": _eval_ok(r),
            }
            for r in runs
        ],
        "changes": [
            {
                "at": c.at,
                "by": c.actor_username,
                "version": c.version,
                "reason": c.reason,
                "changes": c.changes,
            }
            for c in changes
        ],
        "incidents": [
            {"at": n.sent_at, "kind": n.kind, "subject": n.subject, "succeeded": n.succeeded}
            for n in incidents
        ],
    }
    db.commit()
    return out


@router.put("/policy")
def update_policy(
    body: PolicyIn,
    caller: Caller = Depends(require(Permission.MANAGE_AUTOMATION)),
    db: Session = Depends(get_db),
):
    changes = body.model_dump(exclude_none=True, exclude={"reason"})
    if "categories" in changes:
        codes = [c["code"] for c in changes["categories"]]
        if len(set(codes)) != len(codes):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Each category can appear only once.")
        bad = [c["department"] for c in changes["categories"] if c["department"] not in DEPARTMENTS]
        if bad:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown department: {bad[0]}")
        for c in changes["categories"]:
            if c["route"] == "urgent" and c["auto_send"]:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"{c['label']} is routed as urgent and can never be answered automatically.",
                )
    policy = get_policy(db)
    if changes.get("mode") in Mode.AUTONOMOUS and changes["mode"] != policy.mode:
        latest = db.scalar(select(EvalRun).order_by(EvalRun.at.desc()).limit(1))
        if latest is None or utcnow() - as_utc(latest.at) > EVAL_VALID_FOR:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Run the evaluation (python -m app.evals.run) first: autonomous modes need a result "
                "from the last 30 days.",
            )
        if not _eval_ok(latest, changes["mode"]):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "The latest evaluation must be run on the bank's knowledge base with "
                f"--mode {changes['mode']} and have no unsafe automatic answers and no missed escalations.",
            )
    diff = change_policy(db, changes, reason=body.reason, actor=caller.user, ip=caller.ip)
    db.commit()
    return {"changed": sorted(diff), "policy": _policy_out(db)}


@router.post("/kill-switch")
def kill_switch(
    body: KillSwitchIn,
    caller: Caller = Depends(require(Permission.VIEW_AI)),
    db: Session = Depends(get_db),
):
    """Stop (or resume) every automatic answer. Supervisors may stop; only admins may resume."""
    needed = Permission.APPROVE_REPLIES if body.on else Permission.MANAGE_AUTOMATION
    if not (
        has_permission(caller.user.role, needed)
        or has_permission(caller.user.role, Permission.MANAGE_AUTOMATION)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only an administrator can switch automatic answers back on."
            if not body.on
            else "You do not have permission to do this.",
        )
    change_policy(
        db,
        {"kill_switch": body.on, "kill_switch_reason": body.reason if body.on else None},
        reason=("Emergency stop: " if body.on else "Resumed: ") + body.reason,
        actor=caller.user,
        ip=caller.ip,
    )
    db.commit()
    return _policy_out(db)


@router.post("/try")
def try_pipeline(
    body: TryIn,
    caller: Caller = Depends(require(Permission.USE_TEST_AREA)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    sources: DataSources = Depends(get_sources),
):
    """What the assistant would do with this email, end to end. Nothing is sent or stored."""
    result = simulate(
        db,
        subject=body.subject,
        body=body.body,
        sender=body.sender,
        sender_verified=body.sender_verified,
        model=get_model(settings),
        sources=sources,
        settings=settings,
        include_draft_knowledge=body.include_drafts,
        run_queries=has_permission(caller.user.role, Permission.RUN_TEST_QUERIES),
    )
    c = result.classification
    audit.record(db, "ai.tried", actor=caller.user, detail={"route": result.decision.route}, ip=caller.ip)
    db.commit()
    return {
        "classification": {
            "category": c.category,
            "intent": c.intent,
            "language": c.language,
            "urgency": c.urgency,
            "risk": c.risk,
            "sentiment": c.sentiment,
            "department": c.department,
            "summary": c.summary,
            "evidence": c.evidence,
            "next_steps": c.next_steps,
            "prompt_injection": c.injection,
            "urgent_signals": c.urgent_signals,
            "classified_by": c.classified_by,
            "model_error": c.model_error,
        },
        "decision": {
            "route": result.decision.route,
            "send": result.decision.send,
            "reason": result.decision.reason,
            "reason_code": result.decision.reason_code,
            "rules": result.decision.rules,
        },
        "approved_text": result.approved_text,
        "draft": result.draft,
        "draft_error": result.draft_error,
        "draft_checks": result.draft_checks,
        "citations": result.citations,
        "evidence": result.evidence,
    }


@router.get("/audit-integrity")
def audit_integrity(caller: Caller = Depends(require(Permission.VIEW_AUDIT)), db: Session = Depends(get_db)):
    """Recompute the audit trail's hash chain; any edited or deleted event shows up here."""
    from app.services.audit import verify_chain

    result = verify_chain(db)
    audit.record(
        db,
        "audit.verified",
        actor=caller.user,
        detail={"ok": result["ok"], "checked": result["checked"]},
        ip=caller.ip,
    )
    db.commit()
    return result
