"""Reading answers from the bank's existing databases, safely.

Safety layers, outermost first:
  1. The database account entered on the Data Sources page should only have SELECT rights.
     This is the real guarantee; everything below is defence in depth.
  2. Guided topics never contain SQL text: SQLAlchemy builds a SELECT from the chosen table
     and columns. Advanced (hand-written) queries are checked before they are saved: one
     statement, SELECT/WITH only, no comments, no write or admin keywords.
  3. Values from the email are always passed as bound parameters (:name), never pasted into
     the SQL, so an email cannot inject SQL.
  4. Each query runs in a transaction that is rolled back, read-only where the database
     supports it, with a row limit and (on PostgreSQL) a time limit.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import MetaData, Table, bindparam, create_engine, func, inspect, select, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import SecretUnreadable, decrypt
from app.models import DataSource, DataSourceKind

# Values the system can always pass to a query, without any extractor.
BUILTIN_PARAMS = ("sender_email", "sender_name", "subject", "reference")

_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|upsert|drop|alter|create|truncate|grant|revoke|exec|execute|"
    r"call|into|lock|copy|attach|detach|pragma|vacuum|commit|rollback|savepoint|declare|begin)\b",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)(?::(money|number|date|datetime|upper|lower|title))?\}")


class QueryRejected(ValueError):
    """The query is not allowed. The message is safe to show."""


class TemplateError(ValueError):
    """The reply layout refers to something the query did not return."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]


# -- validation ------------------------------------------------------------------------------


def _without_literals(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def query_params(sql: str) -> list[str]:
    return sorted(set(_PARAM.findall(_without_literals(sql))))


def validate_query(sql: str, extractor_names: list[str]) -> str:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise QueryRejected("The query is empty.")
    if ";" in sql:
        raise QueryRejected("Only one statement is allowed (remove the ';').")
    if "--" in sql or "/*" in sql:
        raise QueryRejected("Comments are not allowed in queries.")
    if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        raise QueryRejected("Queries must start with SELECT or WITH.")
    # Ignore quoted text so a word like 'Updated' inside a string literal is not flagged.
    bad = _FORBIDDEN.search(_without_literals(sql))
    if bad:
        raise QueryRejected(f"'{bad.group(1).upper()}' is not allowed; answers may only read data.")
    allowed = set(BUILTIN_PARAMS) | set(extractor_names)
    unknown = [p for p in query_params(sql) if p not in allowed]
    if unknown:
        raise QueryRejected(
            "Unknown parameter(s): "
            + ", ".join(f":{p}" for p in unknown)
            + ". Add an extractor with that name, or use "
            + ", ".join(f":{p}" for p in BUILTIN_PARAMS)
            + "."
        )
    return sql


def validate_extractors(extractors: dict[str, str]) -> None:
    for name, pattern in extractors.items():
        if not re.fullmatch(r"[A-Za-z_]\w{0,40}", name) or name in BUILTIN_PARAMS:
            raise QueryRejected(f"'{name}' is not a valid extractor name.")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise QueryRejected(f"The pattern for '{name}' is not valid: {exc}") from exc


def template_fields(template: str) -> list[str]:
    return sorted({m.group(1) for m in _PLACEHOLDER.finditer(template)})


# -- connections -----------------------------------------------------------------------------

DRIVERS = {
    DataSourceKind.ORACLE: "oracle+oracledb",
    DataSourceKind.SQLSERVER: "mssql+pymssql",
    DataSourceKind.POSTGRESQL: "postgresql+psycopg",
    DataSourceKind.MYSQL: "mysql+pymysql",
}


def connection_url(ds: DataSource, settings: Settings) -> URL:
    if ds.kind == DataSourceKind.SQLITE:
        if settings.is_production:
            raise QueryRejected("SQLite files cannot be used as a data source in production.")
        return URL.create("sqlite", database=ds.database)
    if ds.kind not in DRIVERS:
        raise QueryRejected(f"Unknown database type '{ds.kind}'.")
    password = decrypt(ds.password_encrypted) if ds.password_encrypted else None
    port = ds.port or DataSourceKind.DEFAULT_PORTS.get(ds.kind)
    if ds.kind == DataSourceKind.ORACLE:
        return URL.create(
            DRIVERS[ds.kind],
            username=ds.username,
            password=password,
            host=ds.host,
            port=port,
            query={"service_name": ds.database},
        )
    return URL.create(
        DRIVERS[ds.kind],
        username=ds.username,
        password=password,
        host=ds.host,
        port=port,
        database=ds.database or None,
    )


class DataSources:
    """Connections to the bank's databases (configured in the dashboard), created on first use."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._engines: dict[tuple, Engine] = {}
        self._tables: dict[tuple, Table] = {}
        self._lock = threading.Lock()

    # -- lookup ---------------------------------------------------------------------------

    @staticmethod
    def names(db: Session) -> list[str]:
        return list(
            db.scalars(
                select(DataSource.name).where(DataSource.is_active.is_(True)).order_by(DataSource.name)
            )
        )

    @staticmethod
    def get(db: Session, name: str) -> DataSource:
        ds = db.scalar(select(DataSource).where(DataSource.name == name))
        if ds is None or not ds.is_active:
            raise QueryRejected(f"Data source '{name}' does not exist or is switched off.")
        return ds

    def _key(self, ds: DataSource) -> tuple:
        return (ds.id, ds.updated_at)

    def engine(self, ds: DataSource) -> Engine:
        key = self._key(ds)
        with self._lock:
            if key not in self._engines:
                # Drop engines for older versions of this data source (edited connection).
                for old in [k for k in self._engines if k[0] == ds.id]:
                    self._engines.pop(old).dispose()
                kwargs = {"pool_pre_ping": True}
                if ds.kind != DataSourceKind.SQLITE:
                    kwargs.update(pool_size=2, max_overflow=2)
                self._engines[key] = create_engine(connection_url(ds, self._settings), **kwargs)
            return self._engines[key]

    def forget(self, ds_id) -> None:
        with self._lock:
            for old in [k for k in self._engines if k[0] == ds_id]:
                self._engines.pop(old).dispose()
            for old in [k for k in self._tables if k[0] == ds_id]:
                self._tables.pop(old)

    # -- schema browsing (for the guided topic form) ---------------------------------------

    def tables(self, ds: DataSource) -> list[str]:
        insp = inspect(self.engine(ds))
        names = insp.get_table_names(schema=ds.schema or None)
        try:
            names += insp.get_view_names(schema=ds.schema or None)
        except NotImplementedError:
            pass
        return sorted(set(names), key=str.lower)

    def table(self, ds: DataSource, name: str) -> Table:
        key = (*self._key(ds), name.lower())
        with self._lock:
            cached = self._tables.get(key)
        if cached is not None:
            return cached
        try:
            table = Table(name, MetaData(), autoload_with=self.engine(ds), schema=ds.schema or None)
        except NoSuchTableError as exc:
            raise QueryRejected(f"Table '{name}' was not found in '{ds.name}'.") from exc
        with self._lock:
            self._tables[key] = table
        return table

    def columns(self, ds: DataSource, table_name: str) -> list[dict[str, str]]:
        return [{"name": c.name, "type": str(c.type)} for c in self.table(ds, table_name).columns]

    # -- running ----------------------------------------------------------------------------

    def execute(self, ds: DataSource, statement, params: dict[str, Any], max_rows: int) -> QueryResult:
        """Run a SQLAlchemy statement read-only (see _execute)."""
        return self._execute(ds, statement, params, max_rows)

    def _execute(self, ds: DataSource, statement, params: dict[str, Any], max_rows: int) -> QueryResult:
        engine = self.engine(ds)
        max_rows = max(1, min(max_rows, self._settings.query_max_rows))
        with engine.connect() as conn:
            try:
                if engine.dialect.name == "postgresql":
                    conn.execute(text("SET TRANSACTION READ ONLY"))
                    timeout_ms = self._settings.query_timeout_seconds * 1000
                    conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
                result = conn.execute(statement, params)
                columns = [c.lower() for c in result.keys()]
                rows = [dict(zip(columns, row, strict=False)) for row in result.fetchmany(max_rows)]
            finally:
                conn.rollback()
        return QueryResult(columns, rows)

    def run_sql(self, ds: DataSource, sql: str, params: dict[str, Any], max_rows: int) -> QueryResult:
        return self._execute(ds, text(sql), {k: v for k, v in params.items() if v is not None}, max_rows)

    def run_builder(self, ds: DataSource, spec: dict, params: dict[str, Any], max_rows: int) -> QueryResult:
        statement, bound = build_select(self.table(ds, spec["table"]), spec, params)
        return self._execute(ds, statement, bound, max_rows if spec.get("many") else 1)

    def check(self, ds: DataSource) -> str | None:
        """Connect and run a trivial query. Returns an error message, or None if it works."""
        try:
            engine = self.engine(ds)
            probe = "SELECT 1 FROM DUAL" if engine.dialect.name == "oracle" else "SELECT 1"
            with engine.connect() as conn:
                conn.execute(text(probe))
                conn.rollback()
            return None
        except SecretUnreadable as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - report the kind of failure, never the URL
            return f"Could not connect ({type(exc).__name__})."


# -- guided topics: "read this column from that table where ..." -------------------------------

# Values the guided form can pull out of an email, with the pattern used to find each one.
EMAIL_VALUES: dict[str, tuple[str, str]] = {
    "account_number": ("Account number", r"\b(\d{10,16})\b"),
    "phone_number": ("Phone number", r"(?<!\d)(\+?\d[\d ]{8,14}\d)(?!\d)"),
    "card_last4": ("Last 4 digits of a card", r"(?:ending|last\s*(?:4|four)(?:\s*digits)?)\D{0,15}(\d{4})\b"),
}


def email_value_pattern(condition: dict) -> str:
    if condition.get("value_kind") == "custom":
        return condition.get("pattern") or ""
    return EMAIL_VALUES[condition["value_kind"]][1]


def email_value_label(condition: dict) -> str:
    if condition.get("value_kind") == "custom":
        return condition.get("label") or "the required detail"
    return EMAIL_VALUES[condition["value_kind"]][0].lower()


def builder_extractors(spec: dict) -> dict[str, str]:
    """The email values a guided topic needs, as name -> pattern (one per condition)."""
    return {
        f"v{i}": email_value_pattern(c)
        for i, c in enumerate(spec.get("conditions", []))
        if c.get("source") == "email_value"
    }


# Parameters that do not identify a customer; any other parameter in a hand-written query does.
NON_CUSTOMER_PARAMS = frozenset({"subject", "reference"})


def query_is_customer_specific(sql: str | None, extractors: dict | None) -> bool:
    return bool(extractors) or any(p not in NON_CUSTOMER_PARAMS for p in query_params(sql or ""))


def builder_is_customer_specific(spec: dict) -> bool:
    return any(c.get("source") in ("email_value", "sender_email") for c in spec.get("conditions", []))


def validate_builder(spec: dict) -> dict:
    """Check the shape of a guided topic (the columns themselves are checked against the database)."""
    if not isinstance(spec, dict) or not str(spec.get("table", "")).strip():
        raise QueryRejected("Choose the table that holds the answer.")
    columns = [str(c) for c in spec.get("columns") or [] if str(c).strip()]
    if not columns:
        raise QueryRejected("Choose at least one column to use in the reply.")
    conditions = []
    for c in spec.get("conditions") or []:
        source = c.get("source")
        if not c.get("column"):
            raise QueryRejected("Each condition needs a column.")
        if source == "fixed":
            if str(c.get("value", "")).strip() == "":
                raise QueryRejected(f"Enter the value for '{c['column']}'.")
        elif source == "email_value":
            kind = c.get("value_kind")
            if kind == "custom":
                try:
                    re.compile(c.get("pattern") or "")
                except re.error as exc:
                    raise QueryRejected(f"The pattern for '{c['column']}' is not valid: {exc}") from exc
                if not c.get("pattern"):
                    raise QueryRejected(f"Enter a pattern for '{c['column']}'.")
            elif kind not in EMAIL_VALUES:
                raise QueryRejected(f"Choose what to find in the email for '{c['column']}'.")
        elif source != "sender_email":
            raise QueryRejected(f"Choose how to match '{c['column']}'.")
        conditions.append(
            {
                k: v
                for k, v in c.items()
                if k in ("column", "source", "value", "value_kind", "pattern", "label")
            }
        )
    order = spec.get("order_by") or None
    if order and not order.get("column"):
        order = None
    return {
        "table": spec["table"].strip(),
        "columns": columns,
        "conditions": conditions,
        "order_by": order
        and {"column": order["column"], "direction": "asc" if order.get("direction") == "asc" else "desc"},
        "many": bool(spec.get("many")),
    }


def check_builder_against_table(spec: dict, table: Table) -> None:
    names = {c.name.lower(): c.name for c in table.columns}
    used = [*spec["columns"], *(c["column"] for c in spec["conditions"])]
    if spec.get("order_by"):
        used.append(spec["order_by"]["column"])
    missing = [c for c in used if c.lower() not in names]
    if missing:
        raise QueryRejected(f"Column(s) not found in '{table.name}': " + ", ".join(missing))


def _col(table: Table, name: str):
    for c in table.columns:
        if c.name.lower() == name.lower():
            return c
    raise QueryRejected(f"Column '{name}' was not found in '{table.name}'.")


def build_select(table: Table, spec: dict, params: dict[str, Any]):
    """SQLAlchemy builds the SQL, so table and column names are quoted correctly for each database
    and every value is a bound parameter."""
    statement = select(*[_col(table, c) for c in spec["columns"]])
    bound: dict[str, Any] = {}
    for i, c in enumerate(spec["conditions"]):
        column = _col(table, c["column"])
        key = f"v{i}"
        if c["source"] == "fixed":
            bound[key] = c["value"]
            statement = statement.where(column == bindparam(key))
        elif c["source"] == "sender_email":
            bound[key] = (params.get("sender_email") or "").lower()
            statement = statement.where(func.lower(column) == bindparam(key))
        else:
            bound[key] = params.get(key)
            statement = statement.where(column == bindparam(key))
    if spec.get("order_by"):
        column = _col(table, spec["order_by"]["column"])
        statement = statement.order_by(
            column.asc() if spec["order_by"]["direction"] == "asc" else column.desc()
        )
    return statement, bound


# -- extracting and rendering ----------------------------------------------------------------


def extract_params(extractors: dict[str, str], text_: str) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for name, pattern in extractors.items():
        m = re.search(pattern, text_, re.IGNORECASE)
        values[name] = m.group(1) if m and m.groups() else m.group(0) if m else None
        if values[name] is not None:
            values[name] = values[name].strip()
    return values


def _format(value: Any, fmt: str | None) -> str:
    if value is None:
        return ""
    if fmt == "money":
        try:
            return f"{Decimal(str(value)):,.2f}"
        except Exception:  # noqa: BLE001
            return str(value)
    if fmt == "number":
        try:
            return f"{Decimal(str(value)):,}"
        except Exception:  # noqa: BLE001
            return str(value)
    if fmt in ("date", "datetime") and isinstance(value, str):
        # Some databases (e.g. SQLite) return dates as text.
        try:
            value = datetime.fromisoformat(value.strip())
            if fmt == "date":
                value = value.date()
        except ValueError:
            return value
    if fmt in ("date", "datetime") and isinstance(value, datetime | date):
        if fmt == "date" or not isinstance(value, datetime):
            return value.strftime("%d %B %Y")
        return value.strftime("%d %B %Y, %H:%M")
    if fmt == "upper":
        return str(value).upper()
    if fmt == "lower":
        return str(value).lower()
    if fmt == "title":
        return str(value).title()
    if isinstance(value, datetime):
        return value.strftime("%d %B %Y, %H:%M")
    if isinstance(value, date):
        return value.strftime("%d %B %Y")
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def fill_placeholders(template: str, values: dict[str, Any]) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1).lower()
        if key not in values:
            raise TemplateError(
                f"The reply layout uses {{{m.group(1)}}}, but the query did not return that column."
            )
        return _format(values[key], m.group(2))

    return _PLACEHOLDER.sub(sub, template)


def render_reply(
    reply_template: str, row_template: str | None, rows: list[dict[str, Any]], context: dict[str, Any]
) -> str:
    """Lay out the query result. Only values from the query and the context are ever inserted."""
    first = {**context, **rows[0]} if rows else dict(context)
    values = dict(first)
    if "{rows}" in reply_template:
        if not row_template:
            raise TemplateError("The reply layout uses {rows} but no row layout is set.")
        listing = "\n".join(fill_placeholders(row_template, {**context, **row}) for row in rows)
        return fill_placeholders(reply_template.replace("{rows}", "\x00ROWS\x00"), values).replace(
            "\x00ROWS\x00", listing
        )
    return fill_placeholders(reply_template, values)
