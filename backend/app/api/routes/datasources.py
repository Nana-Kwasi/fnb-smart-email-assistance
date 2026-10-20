"""Bank database connections: admins add and test them; topic editors browse tables and columns."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Caller, require
from app.api.services import get_sources
from app.core.config import Settings, get_settings
from app.core.crypto import encrypt
from app.core.db import get_db, utcnow
from app.models import DataSource, DataSourceKind, TopicRevision
from app.schemas import ColumnOut, DataSourceIn, DataSourceNameOut, DataSourceOut
from app.security.rbac import Permission
from app.services import audit
from app.services.datasources import DataSources, QueryRejected

router = APIRouter(prefix="/data-sources", tags=["data sources"])


def _out(ds: DataSource) -> DataSourceOut:
    return DataSourceOut(
        id=ds.id,
        name=ds.name,
        kind=ds.kind,
        host=ds.host,
        port=ds.port,
        database=ds.database,
        db_schema=ds.schema,
        username=ds.username,
        has_password=bool(ds.password_encrypted),
        is_active=ds.is_active,
        last_check_ok=ds.last_check_ok,
        last_check_at=ds.last_check_at,
        last_error=ds.last_error,
        updated_at=ds.updated_at,
    )


def _load(db: Session, ds_id: uuid.UUID) -> DataSource:
    ds = db.get(DataSource, ds_id)
    if ds is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found.")
    return ds


def _apply(ds: DataSource, body: DataSourceIn, settings: Settings) -> None:
    if body.kind == DataSourceKind.SQLITE and settings.is_production:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "SQLite files cannot be used in production."
        )
    if body.kind != DataSourceKind.SQLITE and not (body.host and body.database):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Enter the server address and the database name."
        )
    ds.name, ds.kind, ds.host, ds.port = body.name, body.kind, body.host, body.port
    ds.database, ds.schema, ds.username, ds.is_active = (
        body.database,
        body.db_schema or None,
        body.username,
        body.is_active,
    )
    if body.password:
        ds.password_encrypted = encrypt(body.password)
    ds.last_check_ok = ds.last_check_at = ds.last_error = None
    ds.updated_at = utcnow()


def _check(db: Session, ds: DataSource, sources: DataSources) -> None:
    error = sources.check(ds)
    ds.last_check_ok, ds.last_check_at, ds.last_error = error is None, utcnow(), error


# -- for topic editors -------------------------------------------------------------------------


@router.get("/names", response_model=list[DataSourceNameOut])
def names(_: Caller = Depends(require(Permission.VIEW_TOPICS)), db: Session = Depends(get_db)):
    rows = db.scalars(select(DataSource).where(DataSource.is_active.is_(True)).order_by(DataSource.name))
    return [DataSourceNameOut(name=d.name, kind=d.kind) for d in rows]


@router.get("/browse/tables", response_model=list[str])
def tables(
    source: str = Query(max_length=64),
    _: Caller = Depends(require(Permission.EDIT_TOPICS)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    try:
        return sources.tables(sources.get(db, source))
    except QueryRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - unreachable database
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Could not read '{source}' ({type(exc).__name__})."
        ) from None


@router.get("/browse/columns", response_model=list[ColumnOut])
def columns(
    source: str = Query(max_length=64),
    table: str = Query(max_length=128),
    _: Caller = Depends(require(Permission.EDIT_TOPICS)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    try:
        return sources.columns(sources.get(db, source), table)
    except QueryRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Could not read '{source}' ({type(exc).__name__})."
        ) from None


# -- for admins --------------------------------------------------------------------------------


@router.get("", response_model=list[DataSourceOut])
def list_sources(_: Caller = Depends(require(Permission.MANAGE_DATA_SOURCES)), db: Session = Depends(get_db)):
    return [_out(d) for d in db.scalars(select(DataSource).order_by(DataSource.name))]


@router.post("", response_model=DataSourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    body: DataSourceIn,
    caller: Caller = Depends(require(Permission.MANAGE_DATA_SOURCES)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    sources: DataSources = Depends(get_sources),
):
    if db.scalar(select(DataSource.id).where(DataSource.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A data source with that name already exists.")
    if not settings.customer_data_answers:
        count = db.scalar(select(func.count()).select_from(DataSource)) or 0
        if count >= settings.phase1_max_data_sources:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Phase 1 allows {settings.phase1_max_data_sources} database. "
                "More can be added once customer data answers are enabled.",
            )
    ds = DataSource()
    _apply(ds, body, settings)
    ds.updated_by_id = caller.user.id
    db.add(ds)
    db.flush()
    _check(db, ds, sources)
    audit.record(
        db,
        "datasource.created",
        actor=caller.user,
        entity_type="datasource",
        entity_id=ds.id,
        detail={"name": ds.name, "kind": ds.kind, "host": ds.host},
        ip=caller.ip,
    )
    db.commit()
    return _out(ds)


@router.put("/{ds_id}", response_model=DataSourceOut)
def update_source(
    ds_id: uuid.UUID,
    body: DataSourceIn,
    caller: Caller = Depends(require(Permission.MANAGE_DATA_SOURCES)),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    sources: DataSources = Depends(get_sources),
):
    ds = _load(db, ds_id)
    if body.name != ds.name:
        in_use = db.scalar(select(TopicRevision.id).where(TopicRevision.data_source == ds.name).limit(1))
        if in_use:
            raise HTTPException(status.HTTP_409_CONFLICT, "Topics use this name, so it cannot be renamed.")
        if db.scalar(select(DataSource.id).where(DataSource.name == body.name)):
            raise HTTPException(status.HTTP_409_CONFLICT, "A data source with that name already exists.")
    _apply(ds, body, settings)
    ds.updated_by_id = caller.user.id
    sources.forget(ds.id)
    _check(db, ds, sources)
    audit.record(
        db,
        "datasource.updated",
        actor=caller.user,
        entity_type="datasource",
        entity_id=ds.id,
        detail={"name": ds.name, "password_changed": bool(body.password)},
        ip=caller.ip,
    )
    db.commit()
    return _out(ds)


@router.post("/{ds_id}/check", response_model=DataSourceOut)
def check_source(
    ds_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.MANAGE_DATA_SOURCES)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    ds = _load(db, ds_id)
    _check(db, ds, sources)
    audit.record(
        db,
        "datasource.checked",
        actor=caller.user,
        entity_type="datasource",
        entity_id=ds.id,
        detail={"ok": ds.last_check_ok},
        ip=caller.ip,
    )
    db.commit()
    return _out(ds)


@router.delete("/{ds_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_source(
    ds_id: uuid.UUID,
    caller: Caller = Depends(require(Permission.MANAGE_DATA_SOURCES)),
    db: Session = Depends(get_db),
    sources: DataSources = Depends(get_sources),
):
    ds = _load(db, ds_id)
    if db.scalar(select(TopicRevision.id).where(TopicRevision.data_source == ds.name).limit(1)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Topics use this data source. Switch it off instead.")
    sources.forget(ds.id)
    audit.record(
        db,
        "datasource.deleted",
        actor=caller.user,
        entity_type="datasource",
        entity_id=ds.id,
        detail={"name": ds.name},
        ip=caller.ip,
    )
    db.delete(ds)
    db.commit()
