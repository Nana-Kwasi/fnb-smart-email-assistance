"""Connections to the bank's own databases, managed by admins in the dashboard.

The password is encrypted with FNB_ENCRYPTION_KEY before it is stored, and it is never sent
back to the browser. Use database accounts that can only SELECT.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class DataSourceKind:
    ORACLE = "oracle"
    SQLSERVER = "sqlserver"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLITE = "sqlite"  # local development and tests; refused in production

    LABELS = {
        ORACLE: "Oracle",
        SQLSERVER: "Microsoft SQL Server",
        POSTGRESQL: "PostgreSQL",
        MYSQL: "MySQL / MariaDB",
        SQLITE: "SQLite file (not for production)",
    }
    DEFAULT_PORTS = {ORACLE: 1521, SQLSERVER: 1433, POSTGRESQL: 5432, MYSQL: 3306}


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    host: Mapped[str] = mapped_column(String(255), default="")
    port: Mapped[int | None] = mapped_column(Integer)
    # Database name; for Oracle the service name; for SQLite the file path.
    database: Mapped[str] = mapped_column(String(255), default="")
    # Where the tables live (Oracle/SQL Server/PostgreSQL schema). Empty = the account's default.
    schema: Mapped[str | None] = mapped_column(String(128))
    username: Mapped[str] = mapped_column(String(128), default="")
    password_encrypted: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("staff_users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
