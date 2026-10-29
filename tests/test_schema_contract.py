"""Schema contract tests.

These tests do not exercise HTTP: they pin the invariants that the challenge
grades, at the layer where they must be enforced — the database. If one of these
fails, the idempotency or stock guarantees cannot be trusted no matter what the
application code does.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORDER_SCHEMA = ROOT / "order_api" / "schema.sql"
PAYMENT_SCHEMA = ROOT / "payment_mock" / "schema.sql"


def _load(schema_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    return conn


@pytest.fixture()
def order_db() -> sqlite3.Connection:
    conn = _load(ORDER_SCHEMA)
    conn.execute(
        "INSERT INTO inventory (sku, name, available, reserved) VALUES (?, ?, ?, ?)",
        ("SKU-1", "Mechanical keyboard", 5, 0),
    )
    conn.commit()
    return conn


@pytest.fixture()
def payment_db() -> sqlite3.Connection:
    return _load(PAYMENT_SCHEMA)


def _insert_order(conn: sqlite3.Connection, key: str = "key-1", sku: str = "SKU-1") -> None:
    conn.execute(
        """
        INSERT INTO orders (id, idempotency_key, request_hash, customer_id, sku,
                            quantity, unit_price_cents, total_cents, status, payment_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (f"ord-{key}", key, "hash", "cust-1", sku, 1, 9900, 9900, "PENDING_PAYMENT", f"pay-{key}"),
    )
    conn.commit()


def test_schemas_apply_and_create_expected_tables(order_db, payment_db) -> None:
    order_tables = {row["name"] for row in order_db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    payment_tables = {row["name"] for row in payment_db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert {"inventory", "orders", "reservations", "order_events"} <= order_tables
    assert "payments" in payment_tables


def test_timestamps_default_without_explicit_value(order_db) -> None:
    _insert_order(order_db)
    row = order_db.execute("SELECT created_at FROM orders").fetchone()
    assert row["created_at"].endswith("Z")
    assert "T" in row["created_at"]


def test_duplicate_idempotency_key_is_rejected(order_db) -> None:
    _insert_order(order_db, key="same-key")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_order(order_db, key="same-key")


def test_duplicate_payment_key_is_rejected(payment_db) -> None:
    payment_db.execute(
        """
        INSERT INTO payments (id, idempotency_key, order_id, amount_cents, mode, status)
        VALUES ('pay-1', 'same-key', 'ord-1', 9900, 'approve', 'APPROVED')
        """
    )
    payment_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        payment_db.execute(
            """
            INSERT INTO payments (id, idempotency_key, order_id, amount_cents, mode, status)
            VALUES ('pay-2', 'same-key', 'ord-1', 9900, 'approve', 'APPROVED')
            """
        )


def test_negative_stock_is_impossible(order_db) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        order_db.execute(
            "UPDATE inventory SET available = -1 WHERE sku = 'SKU-1'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        order_db.execute(
            "UPDATE inventory SET reserved = -1 WHERE sku = 'SKU-1'"
        )


def test_second_reservation_for_same_order_is_rejected(order_db) -> None:
    _insert_order(order_db)
    order_db.execute(
        """
        INSERT INTO reservations (id, order_id, sku, quantity, status)
        VALUES ('res-1', 'ord-key-1', 'SKU-1', 1, 'HELD')
        """
    )
    order_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        order_db.execute(
            """
            INSERT INTO reservations (id, order_id, sku, quantity, status)
            VALUES ('res-2', 'ord-key-1', 'SKU-1', 1, 'HELD')
            """
        )


def test_unknown_status_is_rejected(order_db) -> None:
    _insert_order(order_db)
    with pytest.raises(sqlite3.IntegrityError):
        order_db.execute("UPDATE orders SET status = 'MAYBE_PAID'")


def test_payment_requires_known_mode_and_status(payment_db) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        payment_db.execute(
            """
            INSERT INTO payments (id, idempotency_key, order_id, amount_cents, mode, status)
            VALUES ('pay-1', 'k1', 'ord-1', 100, 'teleport', 'APPROVED')
            """
        )
    with pytest.raises(sqlite3.IntegrityError):
        payment_db.execute(
            """
            INSERT INTO payments (id, idempotency_key, order_id, amount_cents, mode, status)
            VALUES ('pay-2', 'k2', 'ord-1', 100, 'approve', 'PENDING')
            """
        )
