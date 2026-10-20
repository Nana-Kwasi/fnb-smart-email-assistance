from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import as_utc, utcnow
from app.models import AuditChainHead, AuditEvent, StaffUser


def _digest(prev_hash: str | None, event: AuditEvent) -> str:
    payload = json.dumps(
        {
            "prev": prev_hash or "",
            "seq": event.seq,
            "id": str(event.id),
            "at": as_utc(event.at).isoformat(),
            "actor": event.actor_username,
            "action": event.action,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "detail": event.detail,
            "ip": event.ip_address,
        },
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _chain_head(db: Session) -> AuditChainHead:
    # Locked until the caller commits (PostgreSQL), so two processes cannot fork the chain.
    head = db.scalar(select(AuditChainHead).where(AuditChainHead.id == 1).with_for_update())
    if head is None:
        head = AuditChainHead(id=1, last_hash=None, count=0)
        db.add(head)
        db.flush()
    return head


def record(
    db: Session,
    action: str,
    *,
    actor: StaffUser | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """Add an audit event to the current transaction. The caller commits, promptly: the chain
    head stays locked until then.

    Never put email bodies or passwords in ``detail``; it is shown to admins verbatim.
    """
    event = AuditEvent(
        id=uuid.uuid4(),
        at=utcnow(),
        actor_id=actor.id if actor else None,
        actor_username=actor.username if actor else "system",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        detail=json.loads(json.dumps(detail or {}, default=str)),
        ip_address=ip,
    )
    head = _chain_head(db)
    event.seq = (head.count or 0) + 1
    event.prev_hash = head.last_hash
    event.hash = _digest(head.last_hash, event)
    head.last_hash, head.count = event.hash, event.seq
    db.add(event)


def verify_chain(db: Session) -> dict[str, Any]:
    """Recompute every hash in order. Events from before hashing was added are skipped."""
    prev: str | None = None
    checked = 0
    for event in db.scalars(select(AuditEvent).where(AuditEvent.seq.is_not(None)).order_by(AuditEvent.seq)):
        if event.prev_hash != prev or _digest(prev, event) != event.hash:
            return {"ok": False, "checked": checked, "broken_at": str(event.id), "at": event.at}
        prev = event.hash
        checked += 1
    head = db.get(AuditChainHead, 1)
    if head is not None and head.last_hash != prev:
        return {"ok": False, "checked": checked, "broken_at": "latest events missing", "at": None}
    return {"ok": True, "checked": checked, "broken_at": None, "at": None}
