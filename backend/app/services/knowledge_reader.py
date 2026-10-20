"""Turn an uploaded document into knowledge entries (a question/heading and its answer text).

What it understands, in order of preference:
  * Spreadsheets (XLSX/CSV) with "Question" and "Answer" columns: one entry per row. Other
    spreadsheets: one entry per row, titled by the first column.
  * "Q: ... / A: ..." pairs, and lines that end with a question mark followed by their answer.
  * Headings (Word heading styles, Markdown #, short ALL-CAPS lines in PDFs) and the text under them.
  * Tables inside Word documents, like spreadsheets.

Files are only read, never run. Anything that could not be used is reported as a warning so
the uploader can see it before publishing.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field

from app.models import EntryKind

HARD_MAX_BYTES = 50 * 1024 * 1024  # the most Settings may allow
MAX_ENTRIES = 3000
MAX_TITLE = 500
ALLOWED = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"}
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

QUESTION_HEADERS = {"question", "questions", "q", "faq", "query", "customer question", "title", "topic"}
ANSWER_HEADERS = {"answer", "answers", "a", "response", "reply", "content", "description", "details"}
_Q_LABEL = re.compile(r"^\s*(?:q|question)\s*\d*\s*[:.)\-]\s*(.+)$", re.IGNORECASE)
_A_LABEL = re.compile(r"^\s*(?:a|answer)\s*[:.)\-]\s*(.*)$", re.IGNORECASE)
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


class UnreadableDocument(ValueError):
    """The file cannot be used. The message is safe to show."""


@dataclass
class Entry:
    kind: str
    title: str
    content: str


@dataclass
class ReadResult:
    entries: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def extension(filename: str) -> str:
    return ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""


def _clean(text: object) -> str:
    return re.sub(r"[ \t ]+", " ", str(text or "")).strip()


def _first_sentence(text: str, limit: int = 140) -> str:
    sentence = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return sentence[:limit].rstrip() + ("…" if len(sentence) > limit else "")


def _is_question(line: str) -> bool:
    return line.endswith("?") and 8 <= len(line) <= 250


# -- the common text reader ----------------------------------------------------------------------


def _from_blocks(blocks: list[tuple[str, str]], result: ReadResult) -> None:
    """blocks: ("heading" | "text" | "break", text). Groups text under the last question/heading."""
    title, kind, paragraphs, current = None, EntryKind.SECTION, [], []

    def end_paragraph():
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    def flush():
        nonlocal title, kind
        end_paragraph()
        body = "\n\n".join(p for p in paragraphs if p).strip()
        if body:
            result.entries.append(
                Entry(
                    kind if title else EntryKind.SECTION, (title or _first_sentence(body))[:MAX_TITLE], body
                )
            )
        elif title and kind == EntryKind.QA:
            result.warnings.append(f"Question without an answer was skipped: “{title[:80]}”")
        title, kind = None, EntryKind.SECTION
        paragraphs.clear()

    for block_kind, raw in blocks:
        text = _clean(raw)
        if block_kind == "break" or not text:
            end_paragraph()
            continue
        if block_kind == "heading":
            flush()
            title = text
            continue
        q = _Q_LABEL.match(text)
        a = _A_LABEL.match(text)
        if q:
            flush()
            title, kind = q.group(1).strip(), EntryKind.QA
        elif a and title:
            end_paragraph()
            if a.group(1).strip():
                current.append(a.group(1).strip())
        elif _is_question(text) and not current:
            flush()
            title, kind = text, EntryKind.QA
        else:
            current.append(text)
    flush()


def _text_blocks(text: str, *, pdf: bool) -> list[tuple[str, str]]:
    blocks = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            blocks.append(("break", ""))
            continue
        md = _MD_HEADING.match(stripped)
        if md:
            blocks.append(("heading", md.group(1)))
        elif pdf and stripped.isupper() and 3 <= len(stripped) <= 80 and any(c.isalpha() for c in stripped):
            blocks.append(("heading", stripped.title()))
        else:
            blocks.append(("text", stripped))
    return blocks


# -- tables (spreadsheets, CSV, Word tables) ------------------------------------------------------


def _from_rows(rows: list[list[str]], where: str, result: ReadResult) -> None:
    rows = [[_clean(c) for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if len(rows) < 2:
        return
    header = [h.lower() for h in rows[0]]
    q_col = next((i for i, h in enumerate(header) if h in QUESTION_HEADERS), None)
    a_col = next((i for i, h in enumerate(header) if h in ANSWER_HEADERS), None)
    if q_col is not None and a_col is not None and q_col != a_col:
        for r in rows[1:]:
            q = r[q_col] if q_col < len(r) else ""
            a = r[a_col] if a_col < len(r) else ""
            if q and a:
                result.entries.append(Entry(EntryKind.QA, q[:MAX_TITLE], a))
            elif q or a:
                result.warnings.append(
                    f"{where}: a row with a question but no answer (or the reverse) was skipped."
                )
        return
    if len(header) < 2:
        result.warnings.append(
            f"{where}: only one column, so it was skipped. Use Question and Answer columns."
        )
        return
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        details = [f"{rows[0][i]}: {v}" for i, v in enumerate(r[1:], start=1) if v and i < len(rows[0])]
        if details:
            result.entries.append(Entry(EntryKind.SECTION, r[0][:MAX_TITLE], "\n".join(details)))


# -- formats -------------------------------------------------------------------------------------


def _read_pdf(data: bytes, result: ReadResult) -> None:
    from pypdf import PdfReader

    if not data.startswith(b"%PDF"):
        raise UnreadableDocument("This is not a valid PDF file.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UnreadableDocument("This PDF is password-protected. Upload an unprotected copy.")
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except UnreadableDocument:
        raise
    except Exception as exc:  # noqa: BLE001 - damaged file
        raise UnreadableDocument("This PDF could not be read. It may be damaged.") from exc
    if not text.strip():
        raise UnreadableDocument(
            "No text found. Scanned PDFs (pictures of pages) cannot be read; upload a text PDF."
        )
    _from_blocks(_text_blocks(text, pdf=True), result)


def _read_docx(data: bytes, result: ReadResult) -> None:
    from docx import Document

    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise UnreadableDocument(
            "This Word document could not be read. Save it as .docx and try again."
        ) from exc
    blocks = []
    for p in doc.paragraphs:
        style = (p.style.name if p.style is not None else "") or ""
        is_heading = style.lower().startswith(("heading", "title"))
        blocks.append(("heading" if is_heading else "text", p.text))
        blocks.append(("break", ""))
    _from_blocks(blocks, result)
    for n, table in enumerate(doc.tables, start=1):
        _from_rows([[cell.text for cell in row.cells] for row in table.rows], f"Table {n}", result)


def _read_xlsx(data: bytes, result: ReadResult) -> None:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise UnreadableDocument(
            "This Excel file could not be read. Save it as .xlsx and try again."
        ) from exc
    for ws in wb.worksheets:
        rows = [["" if v is None else v for v in row] for row in ws.iter_rows(values_only=True)]
        _from_rows(rows, f"Sheet “{ws.title}”", result)
    wb.close()


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnreadableDocument("The text could not be read.")


def read(filename: str, data: bytes, max_bytes: int = HARD_MAX_BYTES) -> ReadResult:
    ext = extension(filename)
    if ext not in ALLOWED:
        raise UnreadableDocument("Upload a PDF, Word (.docx), Excel (.xlsx), CSV or text file.")
    limit = min(max_bytes, HARD_MAX_BYTES)
    if len(data) > limit:
        raise UnreadableDocument(
            f"The file is larger than the {limit // (1024 * 1024)} MB limit set in Settings."
        )
    if not data:
        raise UnreadableDocument("The file is empty.")
    if ext in (".docx", ".xlsx") and not zipfile.is_zipfile(io.BytesIO(data)):
        raise UnreadableDocument(f"This is not a valid {ext} file.")

    result = ReadResult()
    if ext == ".pdf":
        _read_pdf(data, result)
    elif ext == ".docx":
        _read_docx(data, result)
    elif ext == ".xlsx":
        _read_xlsx(data, result)
    elif ext == ".csv":
        _from_rows(list(csv.reader(io.StringIO(_decode(data)))), "CSV", result)
    else:
        _from_blocks(_text_blocks(_decode(data), pdf=False), result)

    if len(result.entries) > MAX_ENTRIES:
        result.warnings.append(f"Only the first {MAX_ENTRIES} entries were kept.")
        result.entries = result.entries[:MAX_ENTRIES]
    if not result.entries:
        raise UnreadableDocument(
            "No answers were found in this file. Use questions with answers, headings with text, "
            "or a spreadsheet with Question and Answer columns."
        )
    return result


def examples_for(entry_kind: str, title: str, content: str) -> list[str]:
    """What the matcher compares emails with: the question, plus the opening of a section's text."""
    if entry_kind == EntryKind.QA:
        return [title]
    sentences = re.split(r"(?<=[.!?])\s+", content.strip())
    return [title, *[s for s in sentences[:2] if len(s) > 15]]
