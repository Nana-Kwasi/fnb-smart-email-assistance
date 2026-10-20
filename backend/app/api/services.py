"""Shared, long-lived service objects, injected so tests can replace them."""

from __future__ import annotations

from functools import lru_cache

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.services.datasources import DataSources
from app.services.graph_client import GraphClient, GraphError
from app.services.processor import Mailbox


@lru_cache
def get_sources() -> DataSources:
    return DataSources(get_settings())


@lru_cache
def _graph() -> GraphClient:
    return GraphClient(get_settings())


def get_mailbox() -> Mailbox:
    try:
        return _graph()
    except GraphError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
