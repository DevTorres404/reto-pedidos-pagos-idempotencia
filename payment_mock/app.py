"""Payment Mock API — a fake card processor.

Contract highlights (see `doc/fase-01-diseno-estados-y-esquemas.md`):

- The payment row is inserted, with its final status, before the response is
  written. A timeout therefore means "the client never learned the outcome", not
  "no charge happened".
- `Idempotency-Key` is unique at the database level. Replaying a key returns the
  stored payment; reusing it with a different amount is a client bug and returns
  409 instead of silently replying with stale data.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from typing import Any

from flask import Flask, jsonify, request

from payment_mock import db

DEFAULT_DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "payments.db")

VALID_MODES = ("approve", "reject", "timeout")
STATUS_BY_MODE = {
    "approve": "APPROVED",
    "reject": "REJECTED",
    "timeout": "CHARGED_TIMEOUT",
}
FAILURE_REASON_BY_MODE = {
    "reject": "card_declined",
}


def _error(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _serialize(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "payment_id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "order_id": row["order_id"],
        "amount_cents": row["amount_cents"],
        "currency": row["currency"],
        "mode": row["mode"],
        "status": row["status"],
        # The whole point of CHARGED_TIMEOUT: the money moved even though the
        # caller never got an answer.
        "charged": row["status"] != "REJECTED",
        "failure_reason": row["failure_reason"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False
    app.config.from_mapping(
        DATABASE=os.environ.get("PAYMENT_DB", DEFAULT_DATABASE),
        MOCK_TIMEOUT_DELAY=float(os.environ.get("MOCK_TIMEOUT_DELAY", "2.0")),
    )
    if config:
        app.config.update(config)

    db.initialize(app.config["DATABASE"])

    @app.get("/health")
    def health():
        return jsonify({"service": "payment_mock", "status": "ok"})

    @app.post("/payments")
    def create_payment():
        key = (request.headers.get("Idempotency-Key") or "").strip()
        if not key:
            return _error(
                "missing_idempotency_key",
                "Header Idempotency-Key is required",
                400,
            )

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("invalid_payload", "Body must be a JSON object", 400)

        order_id = payload.get("order_id")
        amount = payload.get("amount_cents")
        currency = payload.get("currency", "USD")
        mode = payload.get("mode", "approve")

        if not isinstance(order_id, str) or not order_id.strip():
            return _error("invalid_payload", "order_id is required", 400)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            return _error("invalid_payload", "amount_cents must be a non-negative integer", 400)
        if not isinstance(currency, str) or len(currency) != 3:
            return _error("invalid_payload", "currency must be a 3-letter code", 400)
        if mode not in VALID_MODES:
            return _error(
                "invalid_payload",
                f"mode must be one of {', '.join(VALID_MODES)}",
                400,
            )

        status = STATUS_BY_MODE[mode]

        conn = db.connect(app.config["DATABASE"])
        try:
            try:
                conn.execute(
                    """
                    INSERT INTO payments (id, idempotency_key, order_id, amount_cents,
                                          currency, mode, status, failure_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        key,
                        order_id.strip(),
                        amount,
                        currency.upper(),
                        mode,
                        status,
                        FAILURE_REASON_BY_MODE.get(mode),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    "SELECT * FROM payments WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if existing is None:
                    raise
                if (
                    existing["order_id"] != order_id.strip()
                    or existing["amount_cents"] != amount
                    or existing["currency"] != currency.upper()
                ):
                    return _error(
                        "idempotency_key_reuse",
                        "Idempotency-Key was already used for a different payment",
                        409,
                    )
                response = jsonify(_serialize(existing))
                response.status_code = 200
                response.headers["X-Idempotent-Replay"] = "true"
                return response

            row = conn.execute(
                "SELECT * FROM payments WHERE idempotency_key = ?", (key,)
            ).fetchone()
        finally:
            conn.close()

        # The charge is committed at this point. Only now does the mock pretend to
        # be slow: the caller's read timeout fires, the payment stays recorded.
        if mode == "timeout":
            time.sleep(app.config["MOCK_TIMEOUT_DELAY"])

        return jsonify(_serialize(row)), 201

    @app.get("/payments/<key>")
    def get_payment(key: str):
        conn = db.connect(app.config["DATABASE"])
        try:
            row = conn.execute(
                "SELECT * FROM payments WHERE idempotency_key = ?", (key,)
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return _error("payment_not_found", "No payment registered for that key", 404)
        return jsonify(_serialize(row))

    @app.post("/admin/reset")
    def reset():
        """Demo/test helper: forget every payment so the collection can rerun."""
        conn = db.connect(app.config["DATABASE"])
        try:
            conn.execute("DELETE FROM payments")
        finally:
            conn.close()
        return jsonify({"reset": True})

    return app


if __name__ == "__main__":
    port = int(os.environ.get("PAYMENT_PORT", "5001"))
    create_app().run(host="127.0.0.1", port=port)
