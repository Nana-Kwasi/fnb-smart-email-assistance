"""The dashboard API. Run with: uvicorn app.main:app"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.routes import (
    admin,
    ai,
    analytics,
    auth,
    cases,
    datasources,
    emails,
    faq,
    knowledge,
    overview,
    search,
    test_area,
    topics,
)
from app.api.routes import settings as settings_routes
from app.core.config import get_settings
from app.core.db import as_utc, get_db, utcnow
from app.core.logging import configure_logging
from app.core.migrations import upgrade_to_head
from app.core.ratelimit import CHANGES, SENDS
from app.services.app_settings import get_mailbox_state
from app.services.llm import get_model

configure_logging()
log = logging.getLogger("fnb_email_care")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.auto_migrate and settings.env != "test":
        upgrade_to_head()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.auth_mode == "dev":
        log.warning("DEVELOPMENT SIGN-IN IS ON: any username and password will be accepted.")
    app = FastAPI(
        title="Smart Email Assistance API",
        lifespan=lifespan,
        version="0.1.0",
        # Interactive API docs only outside production.
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and settings.env != "test":
            client = request.client.host if request.client else "unknown"
            limiter = SENDS if request.url.path.endswith("/send") else CHANGES
            if not limiter.allow(client):
                return JSONResponse({"detail": "Too many requests. Please wait a minute."}, status_code=429)
        return await call_next(request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Never send stack traces or internal details to the browser.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Something went wrong. Please try again."}, status_code=500)

    api = APIRouter(prefix="/api")

    @api.get("/health", include_in_schema=False)
    def health(db: Session = Depends(get_db)):
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    @api.get("/ready", include_in_schema=False)
    def ready(db: Session = Depends(get_db)):
        """Ready to do its job: database reachable, mailbox checked recently, model answering.

        Details are kept to ok/not ok so nothing internal leaks to an unauthenticated caller.
        """
        checks = {"database": True}
        db.execute(text("SELECT 1"))
        if settings.graph_configured:
            state = get_mailbox_state(db)
            fresh = state.last_poll_at is not None and utcnow() - as_utc(state.last_poll_at) < timedelta(
                seconds=max(300, settings.poll_interval_seconds * 5)
            )
            checks["mailbox"] = bool(fresh and state.last_poll_ok)
        if settings.llm_enabled:
            checks["model"] = get_model(settings).cached_health()[0]
        ok = all(checks.values())
        db.rollback()
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    for module in (
        auth,
        overview,
        search,
        emails,
        knowledge,
        topics,
        faq,
        datasources,
        test_area,
        settings_routes,
        admin,
        cases,
        ai,
        analytics,
    ):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
