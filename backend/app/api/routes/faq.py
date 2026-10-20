"""The bank's FAQ table: set up once by an admin, then browsed when creating a topic.

A topic made from an FAQ entry is an ordinary guided topic: "read <answer column> from <FAQ
table> where <key column> = <entry key>". Approval, matching and sending are unchanged.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import String as SqlString
from sqlalchemy import cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import Caller, require
from app.api.services import get_sources
from app.core.db import get_db, utcnow
from app.models import AppSettings
from app.security.rbac import Permission
from app.services import audit
from app.services.app_settings import get_app_settings
from app.services.datasources import DataSources, QueryRejected

router = APIRouter(prefix="/faq", tags=["faq"])

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
ENTRY_LIMIT = 50


class FaqConfig(BaseModel):
    data_source: Name
    table: Name
    key_column: Name
    question_column: Name | None = None
    answer_column: Name


class FaqConfigOut(BaseModel):
    configured: bool
    config: FaqConfig | None


class FaqEntry(BaseModel):
    key: str
    question: str | None
    answer: str


def _config(cfg: AppSettings) -> FaqConfig | None:
    if not (cfg.faq_data_source and cfg.faq_table and cfg.faq_key_column and cfg.faq_answer_column):
        return None
    return FaqConfig(
        data_source=cfg.faq_data_source,
        table=cfg.faq_table,
        key_column=cfg.faq_key_column,
        question_column=cfg.faq_question_column,
        answer_column=cfg.faq_answer_column,
    )


@router.get("/config", response_model=FaqConfigOut)
def read_config(_: Caller = Depends(require(Permission.VIEW_TOPICS)), db: Session = Depends(get_db)):
    config = _config(get_app_settings(db))
    db.commit()
    return FaqConfigOut(configured=config is not None, config=config)


@router.put("/config", response_model=FaqConfigOut)
def save_config(
    body: FaqConfig,
    caller: Caller = Depends(require(Permission.MANAGE_DATA_SOURCES)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    try:
        table = sources.table(sources.get(db, body.data_source), body.table)
    except QueryRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - database unreachable
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Could not read the database ({type(exc).__name__})."
        ) from None
    names = {c.name for c in table.columns}
    missing = [c for c in (body.key_column, body.question_column, body.answer_column) if c and c not in names]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Column(s) not in '{body.table}': {', '.join(missing)}"
        )
    cfg = get_app_settings(db)
    cfg.faq_data_source, cfg.faq_table, cfg.faq_key_column = body.data_source, body.table, body.key_column
    cfg.faq_question_column, cfg.faq_answer_column = body.question_column, body.answer_column
    cfg.updated_by_id, cfg.updated_at = caller.user.id, utcnow()
    audit.record(db, "faq.configured", actor=caller.user, detail=body.model_dump(), ip=caller.ip)
    db.commit()
    return FaqConfigOut(configured=True, config=body)


@router.get("/entries", response_model=list[FaqEntry])
def entries(
    q: str | None = Query(None, max_length=100),
    _: Caller = Depends(require(Permission.EDIT_TOPICS)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    """FAQ entries to choose from, optionally filtered by words in the question or answer."""
    config = _config(get_app_settings(db))
    if config is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The FAQ table has not been set up yet.")
    try:
        ds = sources.get(db, config.data_source)
        table = sources.table(ds, config.table)
        key, answer = table.c[config.key_column], table.c[config.answer_column]
        question = table.c[config.question_column] if config.question_column else None
        columns = [key, answer] + ([question] if question is not None else [])
        stmt = select(*columns)
        if q and q.strip():
            like = f"%{q.strip().lower()}%"
            searchable = [answer, cast(key, SqlString)] + ([question] if question is not None else [])
            stmt = stmt.where(or_(*(func.lower(c).like(like) for c in searchable)))
        result = sources.execute(ds, stmt.order_by(key), {}, ENTRY_LIMIT)
    except QueryRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except KeyError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "The FAQ set-up refers to a column that no longer exists."
        ) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Could not read the FAQ ({type(exc).__name__})."
        ) from None
    out = []
    for row in result.rows:
        out.append(
            FaqEntry(
                key=str(row[config.key_column.lower()]),
                question=str(row[config.question_column.lower()]) if config.question_column else None,
                answer=str(row[config.answer_column.lower()] or ""),
            )
        )
    return out
