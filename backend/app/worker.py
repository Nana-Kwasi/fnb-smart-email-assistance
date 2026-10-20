"""Background worker: checks the Outlook mailbox and processes new emails.

Run with: python -m app.worker
Run exactly one worker per mailbox.
"""

from __future__ import annotations

import logging
import signal
import threading

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.services import notifications, outlook_replies, quality, retention
from app.services.attachments import scanner_for
from app.services.datasources import DataSources
from app.services.graph_client import GraphClient
from app.services.llm import get_model
from app.services.processor import poll_once, recover_interrupted, retry_acknowledgements

configure_logging()
log = logging.getLogger("fnb_email_care.worker")


def main() -> None:
    settings = get_settings()
    mailbox = GraphClient(settings)
    sources = DataSources(settings)
    model = get_model(settings)
    scanner = scanner_for(settings)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    with SessionLocal() as db:
        recovered = recover_interrupted(db)
        if recovered:
            log.warning("Moved %d interrupted email(s) to the officer queue", recovered)

    log.info("Watching %s every %ss", settings.graph_mailbox, settings.poll_interval_seconds)
    if model.enabled:
        ok, detail = model.health()
        if ok:
            log.info("AI model: %s", detail)
        else:
            log.warning("AI model not ready: %s", detail)
    log.info("Attachment scanning: %s", "ClamAV" if scanner else "off (attachments are not opened)")
    while not stop.is_set():
        with SessionLocal() as db:
            # Also each cycle, not only at start-up: an email stuck mid-processing must reach a person.
            stuck = recover_interrupted(db)
            if stuck:
                log.warning("Moved %d interrupted email(s) to the officer queue", stuck)
            count = poll_once(db, mailbox, sources, settings, model=model, scanner=scanner)
            for name, job in (
                ("Acknowledgement retries", lambda: retry_acknowledgements(db, mailbox)),
                ("Quality monitor", lambda: quality.check_if_due(db, mailbox, settings)),
            ):
                try:
                    job()
                except Exception as exc:  # noqa: BLE001 - one failing job must not stop the others
                    db.rollback()
                    log.error("%s failed: %s", name, type(exc).__name__)
            try:
                replied = outlook_replies.check_if_due(db, mailbox)
                if replied:
                    log.info("%d waiting email(s) were answered in Outlook", replied)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                log.error("Checking Outlook replies failed: %s", exc)
            notifications.run_due(db, mailbox, settings)
            try:
                removed = retention.purge_if_due(db)
                if removed:
                    log.info("Removed %d email(s) past the retention period", removed)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                log.error("Retention clean-up failed: %s", exc)
        if count:
            log.info("Processed %d new email(s)", count)
        stop.wait(settings.poll_interval_seconds)
    log.info("Worker stopped")


if __name__ == "__main__":
    main()
