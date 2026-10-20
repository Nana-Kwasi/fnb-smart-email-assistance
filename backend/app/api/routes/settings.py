from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import Caller, require
from app.core.config import Settings, get_settings
from app.core.db import get_db, utcnow
from app.models import AppSettings
from app.schemas import SettingsIn, SettingsOut
from app.security.rbac import Permission
from app.services import audit
from app.services.app_settings import get_app_settings

router = APIRouter(prefix="/settings", tags=["settings"])


def _out(cfg: AppSettings, settings: Settings) -> SettingsOut:
    return SettingsOut(
        ack_enabled=cfg.ack_enabled,
        auto_reply_enabled=cfg.auto_reply_enabled,
        confidence_threshold=cfg.confidence_threshold,
        ack_template=cfg.ack_template,
        answer_header=cfg.answer_header,
        answer_footer=cfg.answer_footer,
        sensitive_keywords=list(cfg.sensitive_keywords or []),
        suggest_threshold=cfg.suggest_threshold,
        knowledge_categories=list(cfg.knowledge_categories or []),
        escalation_to=cfg.escalation_to,
        escalation_copy_admins=cfg.escalation_copy_admins,
        max_upload_mb=cfg.max_upload_mb,
        require_second_person=cfg.require_second_person,
        max_auto_answer_chars=cfg.max_auto_answer_chars,
        follow_up_days=cfg.follow_up_days,
        page_size=cfg.page_size,
        session_idle_minutes=cfg.session_idle_minutes,
        retention_months=cfg.retention_months,
        notify_recipients=list(cfg.notify_recipients or []),
        notify_urgent=cfg.notify_urgent,
        notify_overdue=cfg.notify_overdue,
        overdue_after_hours=cfg.overdue_after_hours,
        notify_digest=cfg.notify_digest,
        digest_time=cfg.digest_time,
        notify_system=cfg.notify_system,
        updated_at=cfg.updated_at,
        mailbox=settings.graph_mailbox,
        graph_configured=settings.graph_configured,
        internal_domains=sorted(settings.internal_domains),
    )


@router.get("", response_model=SettingsOut)
def read_settings(
    _: Caller = Depends(require(Permission.VIEW_SETTINGS)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    cfg = get_app_settings(db)
    db.commit()
    return _out(cfg, settings)


@router.put("", response_model=SettingsOut)
def update_settings(
    body: SettingsIn,
    caller: Caller = Depends(require(Permission.EDIT_SETTINGS)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    cfg = get_app_settings(db)
    new = body.model_dump()
    new["sensitive_keywords"] = sorted({k.lower() for k in body.sensitive_keywords})
    if body.suggest_threshold > body.confidence_threshold:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The “suggest” level must be lower than the “answer automatically” level.",
        )
    for key in (
        "max_upload_mb",
        "require_second_person",
        "max_auto_answer_chars",
        "follow_up_days",
        "page_size",
        "session_idle_minutes",
        "retention_months",
    ):
        if new[key] is None:
            new.pop(key)
    if body.knowledge_categories is None:
        new.pop("knowledge_categories")
    else:
        names = [c.name.lower() for c in body.knowledge_categories]
        if len(set(names)) != len(names):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Each category name can only appear once."
            )
        new["knowledge_categories"] = [c.model_dump() for c in body.knowledge_categories]
    escalation_to = (body.escalation_to or "").strip().lower() or None
    new["escalation_to"] = escalation_to
    recipients = sorted({r.strip().lower() for r in body.notify_recipients if r.strip()} - {escalation_to})
    bad = [r for r in recipients + ([escalation_to] if escalation_to else []) if "@" not in r]
    domains = settings.internal_domains
    if domains:
        bad += [
            r
            for r in recipients + ([escalation_to] if escalation_to else [])
            if "@" in r and r.rsplit("@", 1)[1] not in domains
        ]
    if bad:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Alerts can only go to internal addresses"
            + (f" ({', '.join('@' + d for d in sorted(domains))})" if domains else "")
            + ": "
            + ", ".join(bad),
        )
    new["notify_recipients"] = recipients
    changed = {k: v for k, v in new.items() if getattr(cfg, k) != v}
    for key, value in changed.items():
        setattr(cfg, key, value)
    cfg.updated_by_id, cfg.updated_at = caller.user.id, utcnow()
    # Record which settings changed and the new switch/threshold values; templates are long,
    # so only their names are logged.
    audit.record(
        db,
        "settings.updated",
        actor=caller.user,
        entity_type="settings",
        entity_id=1,
        detail={k: (v if not isinstance(v, str) or len(v) < 40 else "(changed)") for k, v in changed.items()},
        ip=caller.ip,
    )
    db.commit()
    return _out(cfg, settings)
