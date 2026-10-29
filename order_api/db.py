"""SQLite access for the Order API."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(database: str | Path) -> sqlite3.Connection:
    """Open a connection in autocommit mode.

    Every write path starts an explicit `BEGIN IMMEDIATE`, so the lock is taken
    before any read of the state it is about to change. Autocommit keeps "no
    transaction" unambiguous: a statement outside `BEGIN` is committed on its own.
    """
    conn = sqlite3.connect(str(database), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
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


def rollback_quietly(conn: sqlite3.Connection) -> None:
    """Roll back if a transaction is open; ignore "no transaction" errors."""
    try:
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        pass
