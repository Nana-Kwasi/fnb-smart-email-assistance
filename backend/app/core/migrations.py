"""Bring the database up to the latest migration when the API starts.

Saves remembering `alembic upgrade head` after pulling new code. Docker already runs it before
the API starts (see Dockerfile); running it again is harmless, as nothing is left to apply.
Only the API does this, never the worker, so two processes never migrate at the same time.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

log = logging.getLogger(__name__)

BACKEND = Path(__file__).resolve().parents[2]


def upgrade_to_head() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))  # works from any directory
    log.info("Applying database migrations (if any)")
    command.upgrade(cfg, "head")
