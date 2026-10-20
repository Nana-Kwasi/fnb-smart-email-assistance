"""Global search. Each person only finds what their role allows them to open."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Caller, require
from app.core.db import get_db
from app.models import CustomerEmail, KnowledgeDocument, StaffUser, Topic
from app.schemas import SearchHit, SearchOut
from app.security.rbac import Permission, has_permission

router = APIRouter(prefix="/search", tags=["search"])

LIMIT_PER_KIND = 6

# Pages, with the permission needed to open each one.
PAGES = [
    ("Overview", "/", Permission.VIEW_OVERVIEW, "Home dashboard"),
    ("Needs attention", "/attention", Permission.VIEW_QUEUE, "Emails a person must answer"),
    ("Knowledge base", "/knowledge", Permission.VIEW_KNOWLEDGE, "The bank documents answers come from"),
    ("Topics", "/topics", Permission.VIEW_TOPICS, "Answers from bank databases (phase 2)"),
    ("New topic", "/topics/new", Permission.EDIT_TOPICS, "Teach the assistant a new question"),
    ("Email log", "/emails", Permission.VIEW_EMAIL_LOG, "Every email and reply"),
    ("Test area", "/test", Permission.USE_TEST_AREA, "Try an email without sending anything"),
    ("Cases", "/cases", Permission.VIEW_CASES, "Customer issues across all their emails"),
    ("AI & automation", "/ai", Permission.VIEW_AI, "Model health, quality, kill switch, policy"),
    ("Analytics", "/analytics", Permission.VIEW_ANALYTICS, "Trends, complaints, workload, backlog"),
    ("Data sources", "/data-sources", Permission.MANAGE_DATA_SOURCES, "Bank database connections"),
    ("Settings", "/settings", Permission.VIEW_SETTINGS, "Wording, switches and alerts"),
    ("Users & audit", "/admin", Permission.VIEW_USERS, "Roles, access and audit trail"),
]


@router.get("", response_model=SearchOut)
def search(
    q: str = Query(min_length=2, max_length=100),
    caller: Caller = Depends(require(Permission.VIEW_OVERVIEW)),
    db: Session = Depends(get_db),
) -> SearchOut:
    role = caller.user.role
    term = q.strip()
    like = f"%{term}%"
    hits: list[SearchHit] = []

    for title, url, perm, subtitle in PAGES:
        if term.lower() in title.lower() and has_permission(role, perm):
            hits.append(SearchHit(kind="page", id=url, title=title, subtitle=subtitle, url=url))

    if has_permission(role, Permission.VIEW_EMAIL_LOG):
        emails = db.scalars(
            select(CustomerEmail)
            .where(
                or_(
                    CustomerEmail.reference.ilike(like),
                    CustomerEmail.from_address.ilike(like),
                    CustomerEmail.from_name.ilike(like),
                    CustomerEmail.subject.ilike(like),
                )
            )
            .order_by(CustomerEmail.reference.ilike(like).desc(), CustomerEmail.received_at.desc())
            .limit(LIMIT_PER_KIND)
        ).unique()
        for e in emails:
            hits.append(
                SearchHit(
                    kind="email",
                    id=str(e.id),
                    title=f"{e.reference} · {e.subject or '(no subject)'}",
                    subtitle=f"{e.from_name or e.from_address} · {e.received_at:%d %b %Y}",
                    url=f"/emails/{e.id}",
                )
            )

    if has_permission(role, Permission.VIEW_KNOWLEDGE):
        stmt = select(KnowledgeDocument).where(
            or_(KnowledgeDocument.name.ilike(like), KnowledgeDocument.category.ilike(like))
        )
        if not has_permission(role, Permission.VIEW_RESTRICTED_KNOWLEDGE):
            stmt = stmt.where(KnowledgeDocument.audience != "restricted")
        docs = db.scalars(stmt.limit(LIMIT_PER_KIND))
        for d in docs:
            hits.append(
                SearchHit(
                    kind="document", id=str(d.id), title=d.name, subtitle=d.category, url=f"/knowledge/{d.id}"
                )
            )

    if has_permission(role, Permission.VIEW_TOPICS):
        for t in db.scalars(select(Topic).options(selectinload(Topic.revisions))):
            shown = t.live_revision or (t.revisions[0] if t.revisions else None)
            if shown and term.lower() in shown.title.lower():
                hits.append(
                    SearchHit(
                        kind="topic",
                        id=str(t.id),
                        title=shown.title,
                        subtitle="Live" if t.live_revision else "Awaiting approval",
                        url=f"/topics/{t.id}",
                    )
                )

    if has_permission(role, Permission.VIEW_USERS):
        users = db.scalars(
            select(StaffUser)
            .where(or_(StaffUser.username.ilike(like), StaffUser.display_name.ilike(like)))
            .limit(LIMIT_PER_KIND)
        )
        for u in users:
            hits.append(
                SearchHit(
                    kind="user",
                    id=str(u.id),
                    title=u.display_name or u.username,
                    subtitle=f"{u.username} · {u.role}",
                    url="/admin",
                )
            )

    return SearchOut(hits=hits[: LIMIT_PER_KIND * 4])
