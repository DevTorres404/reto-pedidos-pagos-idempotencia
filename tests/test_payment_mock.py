"""Payment Mock API behaviour, exercised through HTTP and verified in SQLite."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from payment_mock import db
from payment_mock.app import create_app

PAYLOAD = {"order_id": "ord-1", "amount_cents": 9900, "currency": "USD"}


@pytest.fixture()
def database(tmp_path: Path) -> str:
    return str(tmp_path / "payments.db")


@pytest.fixture()
def app(database: str):
    return create_app({"DATABASE": database, "MOCK_TIMEOUT_DELAY": 0.0, "TESTING": True})


@pytest.fixture()
def client(app):
    return app.test_client()


def _rows(database: str) -> list[dict[str, Any]]:
    conn = db.connect(database)
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM payments").fetchall()]
    finally:
        conn.close()


def _post(client, key: str | None = "key-1", payload: dict[str, Any] | None = None):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post("/payments", json=PAYLOAD if payload is None else payload, headers=headers)


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["service"] == "payment_mock"


def test_approved_payment_is_charged_and_persisted(client, database: str) -> None:
    response = _post(client, payload={**PAYLOAD, "mode": "approve"})

    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == "APPROVED"
    assert body["charged"] is True
    assert body["mode"] == "approve"
    assert body["failure_reason"] is None

    rows = _rows(database)
    assert len(rows) == 1
    assert rows[0]["status"] == "APPROVED"
    assert rows[0]["amount_cents"] == 9900


def test_rejected_payment_is_not_charged(client, database: str) -> None:
    response = _post(client, payload={**PAYLOAD, "mode": "reject"})

    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == "REJECTED"
    assert body["charged"] is False
    assert body["failure_reason"] == "card_declined"
    assert _rows(database)[0]["status"] == "REJECTED"


def test_timeout_mode_is_charged_even_though_the_caller_never_learns_it(
    app, client, database: str
) -> None:
    app.config["MOCK_TIMEOUT_DELAY"] = 0.0

    response = _post(client, payload={**PAYLOAD, "mode": "timeout"})

    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == "CHARGED_TIMEOUT"
    assert body["charged"] is True
    assert _rows(database)[0]["status"] == "CHARGED_TIMEOUT"


def test_missing_idempotency_key_writes_nothing(client, database: str) -> None:
    response = _post(client, key=None)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "missing_idempotency_key"
    assert _rows(database) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"amount_cents": 100},
        {"order_id": "ord-1"},
        {"order_id": "ord-1", "amount_cents": -1},
        {"order_id": "ord-1", "amount_cents": "100"},
        {"order_id": "ord-1", "amount_cents": True},
        {"order_id": "ord-1", "amount_cents": 100, "mode": "teleport"},
        {"order_id": "ord-1", "amount_cents": 100, "currency": "DOLLARS"},
    ],
)
def test_invalid_payload_is_rejected_without_rows(client, database: str, payload) -> None:
    response = _post(client, payload=payload)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_payload"
    assert _rows(database) == []


def test_replay_returns_the_stored_payment_and_keeps_one_row(client, database: str) -> None:
    first = _post(client, key="same-key")
    second = _post(client, key="same-key")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["X-Idempotent-Replay"] == "true"
    assert second.get_json()["payment_id"] == first.get_json()["payment_id"]
    assert len(_rows(database)) == 1


def test_key_reuse_with_a_different_amount_is_a_conflict(client, database: str) -> None:
    _post(client, key="same-key")

    response = _post(client, key="same-key", payload={**PAYLOAD, "amount_cents": 1})

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "idempotency_key_reuse"
    assert len(_rows(database)) == 1


def test_get_payment_by_key(client, database: str) -> None:
    created = _post(client, key="lookup-key")

    response = client.get("/payments/lookup-key")

    assert response.status_code == 200
    assert response.get_json()["payment_id"] == created.get_json()["payment_id"]


def test_get_unknown_key_is_not_found(client) -> None:
    response = client.get("/payments/nope")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "payment_not_found"


def test_slow_mode_persists_the_charge_before_the_response_is_sent(tmp_path: Path) -> None:
    """The core guarantee: while the caller waits, the charge is already on disk."""
    database = str(tmp_path / "slow.db")
    application = create_app(
        {"DATABASE": database, "MOCK_TIMEOUT_DELAY": 1.0, "TESTING": True}
    )
    holder: dict[str, Any] = {}

    def call() -> None:
        with application.test_client() as thread_client:
            holder["response"] = thread_client.post(
                "/payments",
                json={**PAYLOAD, "mode": "timeout"},
                headers={"Idempotency-Key": "slow-key"},
            )

    worker = threading.Thread(target=call)
    worker.start()
    try:
        time.sleep(0.3)
        assert "response" not in holder, "the mock answered before the delay elapsed"
        rows = _rows(database)
        assert len(rows) == 1
        assert rows[0]["status"] == "CHARGED_TIMEOUT"
    finally:
        worker.join(timeout=5)

    assert holder["response"].status_code == 201


def test_payment_survives_a_service_restart(tmp_path: Path) -> None:
    database = str(tmp_path / "restart.db")

    first_app = create_app({"DATABASE": database, "MOCK_TIMEOUT_DELAY": 0.0})
    with first_app.test_client() as first_client:
        created = _post(first_client, key="persisted-key")

    second_app = create_app({"DATABASE": database, "MOCK_TIMEOUT_DELAY": 0.0})
    with second_app.test_client() as second_client:
        response = second_client.get("/payments/persisted-key")

    assert response.status_code == 200
    assert response.get_json()["payment_id"] == created.get_json()["payment_id"]
    assert response.get_json()["status"] == "APPROVED"
