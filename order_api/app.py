"""Order API — orders, inventory, reservations and idempotency.

Rules this module exists to enforce (see `doc/fase-01-diseno-estados-y-esquemas.md`):

- The same idempotency key never creates a second order, reservation or charge.
  The guarantee lives in `UNIQUE (idempotency_key)`, not in process memory.
- Stock is reserved with a conditional `UPDATE`, so it cannot go negative even
  under concurrent requests.
- A charge whose outcome is unknown parks the order in `PAYMENT_UNCERTAIN`. It is
  never retried blindly: reconciliation reads the mock first.
- Every state transition is guarded by the state it expects, so running it twice
  has the effect of running it once.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from typing import Any

from flask import Flask, jsonify, request

from order_api import db, payments

DEFAULT_DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db")

TERMINAL_STATUSES = frozenset({"PAID", "REJECTED", "CANCELLED"})
PAYMENT_MODES = ("approve", "reject", "timeout")
APPROVED_PAYMENT_STATUSES = frozenset({"APPROVED", "CHARGED_TIMEOUT"})

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def _error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _validate(payload: dict[str, Any]) -> str | None:
    customer_id = payload.get("customer_id")
    sku = payload.get("sku")
    quantity = payload.get("quantity")
    unit_price = payload.get("unit_price_cents")
    currency = payload.get("currency", "USD")
    mode = payload.get("payment_mode", "approve")

    if not isinstance(customer_id, str) or not customer_id.strip():
        return "customer_id is required"
    if not isinstance(sku, str) or not sku.strip():
        return "sku is required"
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        return "quantity must be a positive integer"
    if isinstance(unit_price, bool) or not isinstance(unit_price, int) or unit_price < 0:
        return "unit_price_cents must be a non-negative integer"
    if not isinstance(currency, str) or len(currency) != 3:
        return "currency must be a 3-letter code"
    if mode not in PAYMENT_MODES:
        return f"payment_mode must be one of {', '.join(PAYMENT_MODES)}"
    return None


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "customer_id": payload["customer_id"].strip(),
        "sku": payload["sku"].strip(),
        "quantity": payload["quantity"],
        "unit_price_cents": payload["unit_price_cents"],
        "currency": payload.get("currency", "USD").upper(),
        "payment_mode": payload.get("payment_mode", "approve"),
    }


def _request_hash(normalized: dict[str, Any]) -> str:
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _event(
    conn: sqlite3.Connection,
    order_id: str,
    event: str,
    from_status: str | None,
    to_status: str,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO order_events (order_id, event, from_status, to_status, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (order_id, event, from_status, to_status, json.dumps(detail) if detail else None),
    )


def _serialize(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    reservation = conn.execute(
        "SELECT * FROM reservations WHERE order_id = ?", (row["id"],)
    ).fetchone()
    events = conn.execute(
        """
        SELECT event, from_status, to_status, detail, created_at
        FROM order_events WHERE order_id = ? ORDER BY id
        """,
        (row["id"],),
    ).fetchall()

    return {
        "order_id": row["id"],
        "status": row["status"],
        "idempotency_key": row["idempotency_key"],
        "customer_id": row["customer_id"],
        "sku": row["sku"],
        "quantity": row["quantity"],
        "unit_price_cents": row["unit_price_cents"],
        "total_cents": row["total_cents"],
        "currency": row["currency"],
        "payment_mode": row["payment_mode"],
        "payment_key": row["payment_key"],
        "reservation": None
        if reservation is None
        else {
            "reservation_id": reservation["id"],
            "status": reservation["status"],
            "sku": reservation["sku"],
            "quantity": reservation["quantity"],
        },
        "events": [
            {
                "event": event["event"],
                "from": event["from_status"],
                "to": event["to_status"],
                "detail": json.loads(event["detail"]) if event["detail"] else None,
                "at": event["created_at"],
            }
            for event in events
        ],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _settle(
    conn: sqlite3.Connection,
    order_id: str,
    payment_status: str,
    source: str,
) -> None:
    """Move a resolvable order to its final state, at most once.

    Everything is conditional on the state the row is expected to be in. A second
    call finds `HELD` already gone and an order already terminal, so it moves no
    stock and writes no event.
    """
    target = "PAID" if payment_status in APPROVED_PAYMENT_STATUSES else "REJECTED"

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None or row["status"] in TERMINAL_STATUSES:
            conn.execute("ROLLBACK")
            return

        current = row["status"]
        moved_order = conn.execute(
            f"""
            UPDATE orders SET status = ?, updated_at = {NOW}
            WHERE id = ? AND status = ?
            """,
            (target, order_id, current),
        ).rowcount
        if moved_order == 0:
            conn.execute("ROLLBACK")
            return

        moved_reservation = conn.execute(
            f"""
            UPDATE reservations SET status = ?, updated_at = {NOW}
            WHERE order_id = ? AND status = 'HELD'
            """,
            ("CONFIRMED" if target == "PAID" else "RELEASED", order_id),
        ).rowcount

        if moved_reservation:
            if target == "PAID":
                # Confirmed stock leaves the warehouse: reserved goes down, available
                # was already decremented when the reservation was taken.
                moved_stock = conn.execute(
                    f"""
                    UPDATE inventory SET reserved = reserved - ?, updated_at = {NOW}
                    WHERE sku = ? AND reserved >= ?
                    """,
                    (row["quantity"], row["sku"], row["quantity"]),
                ).rowcount
            else:
                # Compensation: the unit returns to the shelf, exactly once.
                moved_stock = conn.execute(
                    f"""
                    UPDATE inventory SET available = available + ?,
                                         reserved = reserved - ?,
                                         updated_at = {NOW}
                    WHERE sku = ? AND reserved >= ?
                    """,
                    (row["quantity"], row["quantity"], row["sku"], row["quantity"]),
                ).rowcount
            if moved_stock != 1:
                raise RuntimeError(
                    f"inventory and reservations disagree for order {order_id}"
                )

        _event(
            conn,
            order_id,
            "PAYMENT_APPROVED" if target == "PAID" else "PAYMENT_REJECTED",
            current,
            target,
            {"payment_status": payment_status, "source": source},
        )
        if moved_reservation:
            _event(
                conn,
                order_id,
                "RESERVATION_CONFIRMED" if target == "PAID" else "RESERVATION_RELEASED",
                target,
                target,
                {"sku": row["sku"], "quantity": row["quantity"]},
            )

        conn.execute("COMMIT")
    except Exception:
        db.rollback_quietly(conn)
        raise


def _replay(
    conn: sqlite3.Connection, existing: sqlite3.Row, request_hash: str
):
    """Answer a repeated key with the stored result, or refuse a different payload."""
    if existing["request_hash"] != request_hash:
        return _error(
            "idempotency_key_reuse",
            "Idempotency-Key was already used with a different payload",
            409,
        )
    response = jsonify(_serialize(conn, existing))
    response.status_code = 200
    response.headers["X-Idempotent-Replay"] = "true"
    return response


def _mark_uncertain(conn: sqlite3.Connection, order_id: str, reason: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        moved = conn.execute(
            f"""
            UPDATE orders SET status = 'PAYMENT_UNCERTAIN', updated_at = {NOW}
            WHERE id = ? AND status = 'PENDING_PAYMENT'
            """,
            (order_id,),
        ).rowcount
        if moved:
            _event(
                conn,
                order_id,
                "PAYMENT_UNKNOWN",
                "PENDING_PAYMENT",
                "PAYMENT_UNCERTAIN",
                {"reason": reason},
            )
        conn.execute("COMMIT")
    except Exception:
        db.rollback_quietly(conn)
        raise


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False
    app.config.from_mapping(
        DATABASE=os.environ.get("ORDER_DB", DEFAULT_DATABASE),
        PAYMENT_BASE_URL=os.environ.get("PAYMENT_BASE_URL", "http://127.0.0.1:5001"),
        # Must stay below the mock's MOCK_TIMEOUT_DELAY, otherwise the simulated
        # timeout is never simulated: the caller would receive the answer.
        PAYMENT_TIMEOUT_SECONDS=float(os.environ.get("PAYMENT_TIMEOUT_SECONDS", "1.0")),
        ALLOW_ADMIN=os.environ.get("ORDER_ALLOW_ADMIN", "true").lower() == "true",
    )
    if config:
        app.config.update(config)

    db.initialize(app.config["DATABASE"])

    def connection() -> sqlite3.Connection:
        return db.connect(app.config["DATABASE"])

    def load(order_id: str) -> tuple[dict[str, Any], str] | None:
        conn = connection()
        try:
            row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if row is None:
                return None
            return _serialize(conn, row), row["status"]
        finally:
            conn.close()

    def admin_guard():
        if not app.config["ALLOW_ADMIN"]:
            return _error("admin_disabled", "Admin endpoints are disabled", 403)
        return None

    @app.get("/health")
    def health():
        return jsonify({"service": "order_api", "status": "ok"})

    # ---------------------------------------------------------------- orders
    @app.post("/orders")
    def create_order():
        key = (request.headers.get("Idempotency-Key") or "").strip()
        if not key:
            return _error(
                "missing_idempotency_key", "Header Idempotency-Key is required", 400
            )

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("invalid_payload", "Body must be a JSON object", 400)

        problem = _validate(payload)
        if problem:
            return _error("invalid_payload", problem, 400)

        data = _normalize(payload)
        request_hash = _request_hash(data)
        total_cents = data["unit_price_cents"] * data["quantity"]

        conn = connection()
        try:
            known = conn.execute(
                "SELECT * FROM orders WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if known is not None:
                return _replay(conn, known, request_hash)

            try:
                conn.execute("BEGIN IMMEDIATE")
                # Existence check first: without it the FK on orders.sku would turn
                # a clean 404 into an integrity error.
                product = conn.execute(
                    "SELECT available FROM inventory WHERE sku = ?", (data["sku"],)
                ).fetchone()
                if product is None:
                    db.rollback_quietly(conn)
                    return _error(
                        "product_not_found", f"No inventory for sku {data['sku']}", 404
                    )

                order_id = f"ord_{uuid.uuid4().hex[:20]}"
                payment_key = f"pay_{uuid.uuid4().hex[:20]}"

                conn.execute(
                    """
                    INSERT INTO orders (id, idempotency_key, request_hash, customer_id,
                                        sku, quantity, unit_price_cents, total_cents,
                                        currency, status, payment_mode, payment_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_PAYMENT', ?, ?)
                    """,
                    (
                        order_id,
                        key,
                        request_hash,
                        data["customer_id"],
                        data["sku"],
                        data["quantity"],
                        data["unit_price_cents"],
                        total_cents,
                        data["currency"],
                        data["payment_mode"],
                        payment_key,
                    ),
                )
                _event(conn, order_id, "ORDER_CREATED", None, "PENDING_PAYMENT")

                reserved = conn.execute(
                    f"""
                    UPDATE inventory SET available = available - ?,
                                         reserved = reserved + ?,
                                         updated_at = {NOW}
                    WHERE sku = ? AND available >= ?
                    """,
                    (data["quantity"], data["quantity"], data["sku"], data["quantity"]),
                ).rowcount

                if reserved == 0:
                    conn.execute(
                        f"UPDATE orders SET status = 'CANCELLED', updated_at = {NOW} WHERE id = ?",
                        (order_id,),
                    )
                    _event(
                        conn,
                        order_id,
                        "STOCK_REJECTED",
                        "PENDING_PAYMENT",
                        "CANCELLED",
                        {
                            "sku": data["sku"],
                            "requested": data["quantity"],
                            "reason": "insufficient_stock",
                        },
                    )
                    conn.execute("COMMIT")
                    return _error(
                        "insufficient_stock",
                        f"Not enough stock for sku {data['sku']}",
                        409,
                    )

                conn.execute(
                    """
                    INSERT INTO reservations (id, order_id, sku, quantity, status)
                    VALUES (?, ?, ?, ?, 'HELD')
                    """,
                    (f"res_{uuid.uuid4().hex[:20]}", order_id, data["sku"], data["quantity"]),
                )
                _event(
                    conn,
                    order_id,
                    "STOCK_RESERVED",
                    "PENDING_PAYMENT",
                    "PENDING_PAYMENT",
                    {"sku": data["sku"], "quantity": data["quantity"]},
                )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                # A concurrent request with the same key won the INSERT.
                db.rollback_quietly(conn)
                existing = conn.execute(
                    "SELECT * FROM orders WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if existing is None:
                    raise
                return _replay(conn, existing, request_hash)
            except Exception:
                db.rollback_quietly(conn)
                raise
        finally:
            conn.close()

        # The order and its reservation are committed. Now the charge.
        try:
            payment = payments.charge(
                app.config["PAYMENT_BASE_URL"],
                payment_key,
                order_id,
                total_cents,
                data["currency"],
                data["payment_mode"],
                app.config["PAYMENT_TIMEOUT_SECONDS"],
            )
        except payments.PaymentUnavailable as exc:
            conn = connection()
            try:
                _mark_uncertain(conn, order_id, str(exc))
            finally:
                conn.close()
            body, _ = load(order_id)
            body["reconcile_url"] = f"/orders/{order_id}/reconcile"
            return jsonify(body), 202

        conn = connection()
        try:
            _settle(conn, order_id, payment["status"], source="create")
        finally:
            conn.close()

        body, _ = load(order_id)
        return jsonify(body), 201

    @app.get("/orders/<order_id>")
    def get_order(order_id: str):
        loaded = load(order_id)
        if loaded is None:
            return _error("order_not_found", "No order with that id", 404)
        return jsonify(loaded[0])

    @app.post("/orders/<order_id>/reconcile")
    def reconcile(order_id: str):
        loaded = load(order_id)
        if loaded is None:
            return _error("order_not_found", "No order with that id", 404)
        body, status = loaded

        if status in TERMINAL_STATUSES:
            return jsonify(body)
        if status not in ("PENDING_PAYMENT", "PAYMENT_UNCERTAIN"):
            return _error("invalid_transition", f"Order is {status}", 409)

        key = body["payment_key"]
        try:
            payment = payments.lookup(
                app.config["PAYMENT_BASE_URL"], key, app.config["PAYMENT_TIMEOUT_SECONDS"]
            )
            if payment is None:
                # The mock never saw the key. Re-issuing is safe because the mock
                # enforces UNIQUE (idempotency_key): at most one charge exists.
                payment = payments.charge(
                    app.config["PAYMENT_BASE_URL"],
                    key,
                    order_id,
                    body["total_cents"],
                    body["currency"],
                    body["payment_mode"],
                    app.config["PAYMENT_TIMEOUT_SECONDS"],
                )
        except payments.PaymentUnavailable as exc:
            conn = connection()
            try:
                _mark_uncertain(conn, order_id, str(exc))
            finally:
                conn.close()
            refreshed, _ = load(order_id)
            refreshed["reconcile_url"] = f"/orders/{order_id}/reconcile"
            return jsonify(refreshed), 202

        conn = connection()
        try:
            _settle(conn, order_id, payment["status"], source="reconcile")
        finally:
            conn.close()

        refreshed, refreshed_status = load(order_id)
        if refreshed_status in TERMINAL_STATUSES:
            return jsonify(refreshed)
        refreshed["reconcile_url"] = f"/orders/{order_id}/reconcile"
        return jsonify(refreshed), 202

    # ------------------------------------------------------------- inventory
    @app.get("/inventory")
    def get_inventory():
        conn = connection()
        try:
            rows = conn.execute(
                "SELECT sku, name, available, reserved FROM inventory ORDER BY sku"
            ).fetchall()
        finally:
            conn.close()
        return jsonify(
            {
                "items": [
                    {
                        "sku": row["sku"],
                        "name": row["name"],
                        "available": row["available"],
                        "reserved": row["reserved"],
                    }
                    for row in rows
                ]
            }
        )

    @app.post("/admin/inventory")
    def upsert_inventory():
        blocked = admin_guard()
        if blocked:
            return blocked

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("invalid_payload", "Body must be a JSON object", 400)

        sku = payload.get("sku")
        name = payload.get("name")
        available = payload.get("available")

        if not isinstance(sku, str) or not sku.strip():
            return _error("invalid_payload", "sku is required", 400)
        if not isinstance(name, str) or not name.strip():
            return _error("invalid_payload", "name is required", 400)
        if isinstance(available, bool) or not isinstance(available, int) or available < 0:
            return _error("invalid_payload", "available must be a non-negative integer", 400)

        conn = connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"""
                INSERT INTO inventory (sku, name, available, reserved)
                VALUES (?, ?, ?, 0)
                ON CONFLICT (sku) DO UPDATE SET
                    name = excluded.name,
                    available = excluded.available,
                    updated_at = {NOW}
                """,
                (sku.strip(), name.strip(), available),
            )
            row = conn.execute(
                "SELECT sku, name, available, reserved FROM inventory WHERE sku = ?",
                (sku.strip(),),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            db.rollback_quietly(conn)
            raise
        finally:
            conn.close()

        return jsonify(
            {
                "sku": row["sku"],
                "name": row["name"],
                "available": row["available"],
                "reserved": row["reserved"],
            }
        )

    # ----------------------------------------------------------------- admin
    @app.post("/admin/reset")
    def reset():
        """Demo/test helper: empty the system so the collection can run again."""
        blocked = admin_guard()
        if blocked:
            return blocked

        conn = connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM order_events")
            conn.execute("DELETE FROM reservations")
            conn.execute("DELETE FROM orders")
            conn.execute("DELETE FROM inventory")
            conn.execute("COMMIT")
        except Exception:
            db.rollback_quietly(conn)
            raise
        finally:
            conn.close()

        return jsonify({"reset": True})

    return app


if __name__ == "__main__":
    port = int(os.environ.get("ORDER_PORT", "5000"))
    create_app().run(host="127.0.0.1", port=port)
