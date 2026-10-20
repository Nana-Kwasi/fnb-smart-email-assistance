"""Workflow actions on a case, run only by staff through the API, never by the model.

Each action declares the permission it needs, validates its parameters with a schema, and says
whether a second person must approve it first. The assistant may *suggest* next steps (free
text shown to officers); it has no way to run anything here.

Actions that would touch other bank systems (ticketing, service status) go through an
integration boundary. Until the bank connects one, they say so plainly and record the request
in the case, rather than pretending something happened elsewhere. Nothing here moves money or
changes account security: a fee reversal request, for example, is approved here and then
carried out by staff in the core banking system under the bank's procedure.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.models import DEPARTMENTS, ActionStatus, AiDraft, Case, CaseAction, CaseStatus, StaffUser
from app.security.rbac import Permission
from app.services import audit, cases

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class ActionError(ValueError):
    """The action cannot run. The message is safe to show."""


class AssignDepartment(BaseModel):
    department: str

    @field_validator("department")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in DEPARTMENTS:
            raise ValueError(f"Unknown department; choose one of: {', '.join(DEPARTMENTS)}")
        return value


class ScheduleCallback(BaseModel):
    when: datetime
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


class RequestDocuments(BaseModel):
    documents: list[Short] = Field(min_length=1, max_length=8)


class SetStatus(BaseModel):
    status: Literal["open", "closed"]
    reason: Short


class CreateTicket(BaseModel):
    summary: Short


class CheckServiceStatus(BaseModel):
    service: Short


class FeeReversalRequest(BaseModel):
    amount: float = Field(gt=0, le=1_000_000)
    reason: Short


@dataclass
class ActionSpec:
    name: str
    label: str
    description: str
    schema: type[BaseModel]
    needs_approval: bool
    run: Callable[[Session, Case, Any, StaffUser], str]
    permission: Permission = Permission.HANDLE_CASES


def _assign_department(db: Session, case: Case, p: AssignDepartment, actor: StaffUser) -> str:
    before = case.department
    case.department = p.department
    case.handoffs = (case.handoffs or 0) + 1
    return f"Moved from {before or 'no department'} to {p.department}"


def _schedule_callback(db: Session, case: Case, p: ScheduleCallback, actor: StaffUser) -> str:
    return f"Callback scheduled for {p.when:%d %b %Y %H:%M}" + (f" ({p.note})" if p.note else "")


def _request_documents(db: Session, case: Case, p: RequestDocuments, actor: StaffUser) -> str:
    from sqlalchemy import select

    from app.models import CustomerEmail, EmailStatus

    email = db.scalar(
        select(CustomerEmail)
        .where(CustomerEmail.case_id == case.id, CustomerEmail.status == EmailStatus.QUEUED)
        .order_by(CustomerEmail.received_at.desc())
        .limit(1)
    )
    if email is None:
        raise ActionError("There is no waiting email in this case to reply to.")
    items = "\n".join(f"- {d}" for d in p.documents)
    name = case.customer_name if case.customer_name and "@" not in case.customer_name else "Valued Customer"
    text = (
        f"Dear {name},\n\nThank you for contacting FNB about {email.reference}. To continue, please "
        f"reply to this email with the following:\n\n{items}\n\nPlease do not send your PIN, password or "
        "one-time codes. We never ask for them.\n\nKind regards,\nFNB Customer Care"
    )
    db.add(AiDraft(email_id=email.id, body_text=text, model="template: request documents", passed=False))
    return "A reply asking for the documents was prepared on the email page; check it and send it"


def _set_status(db: Session, case: Case, p: SetStatus, actor: StaffUser) -> str:
    if p.status == "closed":
        case.status, case.resolved_at = CaseStatus.CLOSED, utcnow()
    else:
        case.status, case.resolved_at = CaseStatus.OPEN, None
    return f"Case {p.status}: {p.reason}"


def _create_ticket(db: Session, case: Case, p: CreateTicket, actor: StaffUser) -> str:
    ref = f"LOCAL-{secrets.token_hex(3).upper()}"
    return (
        f"Recorded as {ref}. No external ticketing system is connected yet, so create the ticket there "
        "by hand if your procedure requires one."
    )


def _check_service_status(db: Session, case: Case, p: CheckServiceStatus, actor: StaffUser) -> str:
    raise ActionError("No service-status system is connected yet. Check the status page your team uses.")


def _fee_reversal(db: Session, case: Case, p: FeeReversalRequest, actor: StaffUser) -> str:
    return (
        f"Fee reversal of {p.amount:,.2f} approved ({p.reason}). Carry it out in the core banking system "
        "following the bank's procedure; this system does not move money."
    )


ACTIONS: dict[str, ActionSpec] = {
    a.name: a
    for a in [
        ActionSpec(
            "assign_department",
            "Assign to a department",
            "Hand the case to another team.",
            AssignDepartment,
            False,
            _assign_department,
        ),
        ActionSpec(
            "schedule_callback",
            "Schedule a callback",
            "Note when to call the customer back.",
            ScheduleCallback,
            False,
            _schedule_callback,
        ),
        ActionSpec(
            "request_documents",
            "Request documents",
            "Prepare a reply asking for missing documents.",
            RequestDocuments,
            False,
            _request_documents,
        ),
        ActionSpec(
            "set_status",
            "Open or close the case",
            "Close a case that needs nothing more, or reopen it.",
            SetStatus,
            False,
            _set_status,
        ),
        ActionSpec(
            "create_ticket",
            "Create a support ticket",
            "Record a ticket (no external system connected).",
            CreateTicket,
            False,
            _create_ticket,
        ),
        ActionSpec(
            "check_service_status",
            "Check service status",
            "Look up an approved service-status system (not connected yet).",
            CheckServiceStatus,
            False,
            _check_service_status,
        ),
        ActionSpec(
            "fee_reversal_request",
            "Request a fee reversal",
            "Financial: needs a second person's approval; carried out by staff in core banking.",
            FeeReversalRequest,
            True,
            _fee_reversal,
        ),
    ]
}


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": a.name,
            "label": a.label,
            "description": a.description,
            "needs_approval": a.needs_approval,
            "params": a.schema.model_json_schema(),
        }
        for a in ACTIONS.values()
    ]


def _parse(spec: ActionSpec, params: dict[str, Any]) -> BaseModel:
    try:
        return spec.schema.model_validate(params)
    except (ValidationError, ValueError) as exc:
        first = exc.errors()[0] if isinstance(exc, ValidationError) else None
        msg = (first["msg"] if first else str(exc)).removeprefix("Value error, ")
        raise ActionError(f"Check the details: {msg}") from None


def _execute(db: Session, row: CaseAction, case: Case, spec: ActionSpec, actor: StaffUser) -> None:
    try:
        row.result = spec.run(db, case, _parse(spec, row.params), actor)[:500]
        row.status = ActionStatus.DONE
    except ActionError as exc:
        row.status, row.result = ActionStatus.FAILED, str(exc)[:500]
    cases.add_event(
        db,
        case,
        f"action_{row.status}",
        actor=actor,
        detail={"action": spec.label, "result": row.result, "params": row.params},
    )


def request(
    db: Session, case: Case, name: str, params: dict[str, Any], actor: StaffUser, ip: str | None = None
) -> CaseAction:
    spec = ACTIONS.get(name)
    if spec is None:
        raise ActionError("Unknown action.")
    parsed = _parse(spec, params)
    row = CaseAction(
        case_id=case.id,
        action=name,
        params=parsed.model_dump(mode="json"),
        status=ActionStatus.PENDING_APPROVAL,
        requested_by_id=actor.id,
    )
    db.add(row)
    db.flush()
    if spec.needs_approval:
        cases.add_event(
            db, case, "action_requested", actor=actor, detail={"action": spec.label, "params": row.params}
        )
    else:
        _execute(db, row, case, spec, actor)
    audit.record(
        db,
        "case.action",
        actor=actor,
        entity_type="case",
        entity_id=case.id,
        detail={"action": name, "status": row.status, "params": row.params},
        ip=ip,
    )
    return row


def decide(
    db: Session, row: CaseAction, approve: bool, actor: StaffUser, ip: str | None = None
) -> CaseAction:
    if row.status != ActionStatus.PENDING_APPROVAL:
        raise ActionError("This action has already been decided.")
    if row.requested_by_id == actor.id:
        raise ActionError("Someone other than the requester must approve it.")
    case = db.get(Case, row.case_id)
    row.decided_by_id, row.decided_at = actor.id, utcnow()
    if approve:
        _execute(db, row, case, ACTIONS[row.action], actor)
    else:
        row.status, row.result = ActionStatus.REJECTED, "Rejected"
        cases.add_event(
            db, case, "action_rejected", actor=actor, detail={"action": ACTIONS[row.action].label}
        )
    audit.record(
        db,
        "case.action_decided",
        actor=actor,
        entity_type="case",
        entity_id=case.id,
        detail={"action": row.action, "approved": approve, "status": row.status},
        ip=ip,
    )
    return row
