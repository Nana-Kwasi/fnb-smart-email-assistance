"""Logging set-up shared by the API and the worker, with customer data masked.

Code here never logs email bodies on purpose, but an error message from a library can still
carry an address or an account number. The filter masks those before anything is written.
"""

from __future__ import annotations

import logging
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Account, card and phone numbers: 7 or more digits, optionally separated by spaces or dashes.
_LONG_NUMBER = re.compile(r"(?<![\w.])\+?\d(?:[ -]?\d){6,}(?![\w.])")


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    return _LONG_NUMBER.sub("[number]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string must not stop logging
            return True
        masked = redact(message)
        if masked != message:
            record.msg, record.args = masked, None
        return True


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
