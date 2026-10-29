"""SQLite access for the Payment Mock API."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(database: str | Path) -> sqlite3.Connection:
    """Open a connection in autocommit mode.

    Autocommit is deliberate: the mock has to persist the charge before it writes
    the HTTP response, and nothing may sit in an uncommitted transaction while the
    process is pretending to hang.
    """
    conn = sqlite3.connect(str(database), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(database) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize(database: str | Path) -> None:
    """Create the database file and apply the schema idempotently."""
    if str(database) != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)

    conn = connect(database)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    finally:
        conn.close()
