"""Customer attachments: check type and size, scan for malware, then read the text.

Nothing is opened before a clean scan. Without a scanner (FNB_CLAMD_HOST unset) attachments
are listed for staff and never read. Extracted text is untrusted: it is shown to staff and
given to the classifier as data, and instructions inside it are flagged, never followed.
The file itself is not stored; staff open the original in Outlook.
"""

from __future__ import annotations

import hashlib
import logging
import socket
import struct
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings
from app.models import Attachment, ScanStatus
from app.services import safety
from app.services.knowledge_reader import UnreadableDocument, read

log = logging.getLogger(__name__)

READABLE = {".pdf", ".docx", ".xlsx", ".csv", ".txt"}
IMAGES = {".png", ".jpg", ".jpeg"}
MAX_TEXT = 20_000


@dataclass
class RawAttachment:
    name: str
    content_type: str
    size: int
    data: bytes | None  # None when it was not downloaded (too big, or not a file)
    is_inline: bool = False


class Scanner(Protocol):
    def scan(self, data: bytes) -> tuple[str, str]: ...  # (status, detail)


class ClamdScanner:
    """ClamAV daemon over TCP (INSTREAM). No extra Python package needed."""

    CHUNK = 64 * 1024

    def __init__(self, host: str, port: int, timeout: float = 30):
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, data: bytes) -> tuple[str, str]:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall(b"zINSTREAM\0")
                for i in range(0, len(data), self.CHUNK):
                    chunk = data[i : i + self.CHUNK]
                    sock.sendall(struct.pack("!L", len(chunk)) + chunk)
                sock.sendall(struct.pack("!L", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    part = sock.recv(4096)
                    if not part:
                        break
                    reply += part
        except OSError as exc:
            return ScanStatus.ERROR, f"Scanner unreachable ({type(exc).__name__})"
        text = reply.rstrip(b"\0").decode(errors="replace")
        if text.endswith("OK"):
            return ScanStatus.CLEAN, "No threat found"
        if text.endswith("FOUND"):
            return ScanStatus.INFECTED, text.split(":", 1)[-1].strip()[:200]
        return ScanStatus.ERROR, text[:200] or "No answer from the scanner"


def scanner_for(settings: Settings) -> Scanner | None:
    return ClamdScanner(settings.clamd_host, settings.clamd_port) if settings.clamd_host else None


def _ext(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def process(
    raw: list[RawAttachment], scanner: Scanner | None, max_bytes: int
) -> tuple[list[Attachment], str | None]:
    """Attachment rows to store, and why a person must look (None if nothing needs one)."""
    rows: list[Attachment] = []
    issues: list[str] = []
    for a in raw:
        ext = _ext(a.name)
        if a.is_inline and ext in IMAGES:
            continue  # logos in signatures
        row = Attachment(name=a.name[:255], content_type=a.content_type[:120], size_bytes=a.size, problems=[])
        rows.append(row)
        if ext not in READABLE | IMAGES:
            row.scan_status, row.scan_detail = (
                ScanStatus.REJECTED,
                f"File type {ext or '(none)'} is not accepted",
            )
            issues.append(f"{a.name}: file type not accepted")
            continue
        if a.size > max_bytes or a.data is None:
            row.scan_status, row.scan_detail = ScanStatus.REJECTED, "Larger than the allowed size"
            issues.append(f"{a.name}: too large to check")
            continue
        row.sha256 = hashlib.sha256(a.data).hexdigest()
        if scanner is None:
            row.scan_status, row.scan_detail = ScanStatus.NOT_SCANNED, "No virus scanner configured"
            issues.append(f"{a.name}: not scanned")
            continue
        row.scan_status, row.scan_detail = scanner.scan(a.data)
        if row.scan_status != ScanStatus.CLEAN:
            issues.append(
                f"{a.name}: {'malware found' if row.scan_status == ScanStatus.INFECTED else 'scan failed'}"
            )
            continue
        if ext in IMAGES:
            row.problems = ["Image: a person needs to look at it"]
            issues.append(f"{a.name}: image needs a person")
            continue
        try:
            parsed = read(a.name, a.data, max_bytes=max_bytes)
            text = "\n\n".join(f"{e.title}\n{e.content}" for e in parsed.entries)
        except UnreadableDocument as exc:
            row.problems = [f"Could not read the text ({exc})"]
            issues.append(f"{a.name}: unreadable")
            continue
        except Exception as exc:  # noqa: BLE001 - a malformed file must not stop the email
            log.warning("Reading an attachment failed: %s", type(exc).__name__)
            row.problems = ["Could not read the text"]
            issues.append(f"{a.name}: unreadable")
            continue
        row.extracted_text = text[:MAX_TEXT] or None
        if not text.strip():
            row.problems = ["No text found (scanned image or empty file?)"]
            issues.append(f"{a.name}: no readable text")
        if safety.injection_signals(text):
            row.problems = [*row.problems, "Contains instructions aimed at the assistant (ignored)"]
            issues.append(f"{a.name}: contains instructions aimed at the assistant")
    return rows, ("Attachments need a person: " + "; ".join(issues[:4])) if issues else None
