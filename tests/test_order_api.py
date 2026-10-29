"""End-to-end behaviour of the Order API against a live Payment Mock.

The mock runs as a real HTTP server in a background thread, so timeouts are real
client timeouts and not monkeypatched exceptions. Every assertion on counts is
read from SQLite, never from the response alone: the point of the challenge is
proving that effects were not duplicated.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from werkzeug.serving import make_server

from order_api.app import create_app as create_order_app
from payment_mock.app import create_app as create_payment_app

SKU = "SKU-1"
DEAD_URL = "http://127.0.0.1:9"


def _order_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "customer_id": "cust-1",
        "sku": SKU,
        "quantity": 1,
        "unit_price_cents": 9900,
    }
    payload.update(overrides)
    return payload


def _rows(database: str, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.fixture()
def mock_server(tmp_path: Path):
    application = create_payment_app(
        {
            "DATABASE": str(tmp_path / "payments.db"),
            "MOCK_TIMEOUT_DELAY": 1.5,
            "TESTING": True,
        }
    )
    server = make_server("127.0.0.1", 0, application, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{server.server_port}",
            "database": str(tmp_path / "payments.db"),
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def order_database(tmp_path: Path) -> str:
    return str(tmp_path / "orders.db")


@pytest.fixture()
def order_app(order_database: str, mock_server):
    return create_order_app(
        {
            "DATABASE": order_database,
            "PAYMENT_BASE_URL": mock_server["url"],
            "PAYMENT_TIMEOUT_SECONDS": 0.4,
            "TESTING": True,
        }
    )


@pytest.fixture()
def client(order_app):
    return order_app.test_client()


def seed(client, available: int = 5, sku: str = SKU):
    response = client.post(
        "/admin/inventory",
        json={"sku": sku, "name": "Mechanical keyboard", "available": available},
    )
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def post_order(client, key: str | None, payload: dict[str, Any] | None = None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/orders", json=payload or _order_payload(), headers=headers)


# --------------------------------------------------------------- case 1
def test_happy_path_creates_one_order_one_reservation_one_charge(
    client, order_database: str, mock_server
) -> None:
    seed(client, 5)

    response = post_order(client, "key-happy")

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["status"] == "PAID"
    assert body["total_cents"] == 9900
    assert body["reservation"]["status"] == "CONFIRMED"
    assert [event["event"] for event in body["events"]] == [
        "ORDER_CREATED",
        "STOCK_RESERVED",
        "PAYMENT_APPROVED",
        "RESERVATION_CONFIRMED",
    ]

    inventory = _rows(order_database, "SELECT available, reserved FROM inventory")[0]
    assert (inventory["available"], inventory["reserved"]) == (4, 0)
    assert len(_rows(order_database, "SELECT * FROM orders")) == 1
    assert len(_rows(order_database, "SELECT * FROM reservations")) == 1
    assert len(_rows(mock_server["database"], "SELECT * FROM payments")) == 1
    assert _rows(mock_server["database"], "SELECT status FROM payments")[0]["status"] == "APPROVED"


# --------------------------------------------------------------- case 2
def test_replay_with_the_same_key_changes_nothing(client, order_database: str, mock_server) -> None:
    seed(client, 5)

    first = post_order(client, "key-replay")
    second = post_order(client, "key-replay")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["X-Idempotent-Replay"] == "true"
    assert second.get_json()["order_id"] == first.get_json()["order_id"]

    inventory = _rows(order_database, "SELECT available, reserved FROM inventory")[0]
    assert (inventory["available"], inventory["reserved"]) == (4, 0)
    assert len(_rows(order_database, "SELECT * FROM orders")) == 1
    assert len(_rows(order_database, "SELECT * FROM reservations")) == 1
    assert len(_rows(mock_server["database"], "SELECT * FROM payments")) == 1


def test_same_key_with_a_different_payload_is_refused(client, order_database: str) -> None:
    seed(client, 5)

    post_order(client, "key-reuse")
    response = post_order(client, "key-reuse", _order_payload(quantity=2))

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "idempotency_key_reuse"
    assert len(_rows(order_database, "SELECT * FROM orders")) == 1


# --------------------------------------------------------------- case 3
def test_lost_response_is_uncertain_and_reconciles_without_a_second_charge(
    client, order_database: str, mock_server
) -> None:
    seed(client, 5)

    created = post_order(client, "key-lost", _order_payload(payment_mode="timeout"))

    assert created.status_code == 202, created.get_json()
    body = created.get_json()
    assert body["status"] == "PAYMENT_UNCERTAIN"
    assert body["reconcile_url"] == f"/orders/{body['order_id']}/reconcile"

    payments = _rows(mock_server["database"], "SELECT status FROM payments")
    assert len(payments) == 1
    assert payments[0]["status"] == "CHARGED_TIMEOUT"

    reconciled = client.post(f"/orders/{body['order_id']}/reconcile")

    assert reconciled.status_code == 200, reconciled.get_json()
    final = reconciled.get_json()
    assert final["status"] == "PAID"
    assert final["reservation"]["status"] == "CONFIRMED"
    assert [event["event"] for event in final["events"]] == [
        "ORDER_CREATED",
        "STOCK_RESERVED",
        "PAYMENT_UNKNOWN",
        "PAYMENT_APPROVED",
        "RESERVATION_CONFIRMED",
    ]
    assert len(_rows(mock_server["database"], "SELECT * FROM payments")) == 1

    inventory = _rows(order_database, "SELECT available, reserved FROM inventory")[0]
    assert (inventory["available"], inventory["reserved"]) == (4, 0)


def test_reconcile_is_idempotent_on_a_paid_order(client, mock_server) -> None:
    seed(client, 5)
    created = post_order(client, "key-reconcile-twice", _order_payload(payment_mode="timeout"))
    order_id = created.get_json()["order_id"]
    client.post(f"/orders/{order_id}/reconcile")

    again = client.post(f"/orders/{order_id}/reconcile")

    assert again.status_code == 200
    assert again.get_json()["status"] == "PAID"
    assert len(_rows(mock_server["database"], "SELECT * FROM payments")) == 1


def test_reconcile_reissues_when_the_charge_never_reached_the_mock(
    order_database: str, mock_server
) -> None:
    # Point the order API at a dead port: the charge cannot arrive.
    application = create_order_app(
        {
            "DATABASE": order_database,
            "PAYMENT_BASE_URL": DEAD_URL,
            "PAYMENT_TIMEOUT_SECONDS": 0.4,
            "TESTING": True,
        }
    )
    with application.test_client() as client:
        seed(client, 5)
        created = post_order(client, "key-never-arrived")
        assert created.status_code == 202
        assert created.get_json()["status"] == "PAYMENT_UNCERTAIN"
        order_id = created.get_json()["order_id"]

        # The mock is alive again; reconciliation finds no payment and re-issues.
        application.config["PAYMENT_BASE_URL"] = mock_server["url"]
        reconciled = client.post(f"/orders/{order_id}/reconcile")

    assert reconciled.status_code == 200
    assert reconciled.get_json()["status"] == "PAID"
    assert len(_rows(mock_server["database"], "SELECT * FROM payments")) == 1


# --------------------------------------------------------------- case 4
def test_rejected_payment_releases_the_reservation_exactly_once(
    client, order_database: str, mock_server
) -> None:
    seed(client, 5)

    response = post_order(client, "key-reject", _order_payload(payment_mode="reject"))

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["status"] == "REJECTED"
    assert body["reservation"]["status"] == "RELEASED"

    inventory = _rows(order_database, "SELECT available, reserved FROM inventory")[0]
    assert (inventory["available"], inventory["reserved"]) == (5, 0)
    assert len(_rows(mock_server["database"], "SELECT * FROM payments")) == 1

    released = _rows(
        order_database,
        "SELECT COUNT(*) AS n FROM order_events WHERE event = 'RESERVATION_RELEASED'",
    )[0]["n"]
    assert released == 1

    # A second reconcile on a terminal order must not release again.
    again = client.post(f"/orders/{body['order_id']}/reconcile")
    assert again.status_code == 200
    inventory = _rows(order_database, "SELECT available, reserved FROM inventory")[0]
    assert (inventory["available"], inventory["reserved"]) == (5, 0)


# --------------------------------------------------------------- case 5
def test_last_unit_goes_to_one_intent_and_stock_never_goes_negative(
    client, order_database: str
) -> None:
    seed(client, 1)

    first = post_order(client, "key-intent-a")
    second = post_order(client, "key-intent-b")

    assert first.status_code == 201
    assert first.get_json()["status"] == "PAID"
    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "insufficient_stock"

    inventory = _rows(order_database, "SELECT available, reserved FROM inventory")[0]
    assert (inventory["available"], inventory["reserved"]) == (0, 0)

    statuses = {row["status"] for row in _rows(order_database, "SELECT status FROM orders")}
    assert statuses == {"PAID", "CANCELLED"}


def test_unknown_product_is_not_found(client, order_database: str) -> None:
    response = post_order(client, "key-missing-sku", _order_payload(sku="NOPE"))

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "product_not_found"
    assert len(_rows(order_database, "SELECT * FROM reservations")) == 0


# ------------------------------------------------------------- contract
def test_missing_idempotency_key_is_rejected(client, order_database: str) -> None:
    response = post_order(client, None)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "missing_idempotency_key"
    assert len(_rows(order_database, "SELECT * FROM orders")) == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"sku": SKU, "quantity": 1, "unit_price_cents": 100},
        {"customer_id": "c", "quantity": 1, "unit_price_cents": 100},
        {"customer_id": "c", "sku": SKU, "quantity": 0, "unit_price_cents": 100},
        {"customer_id": "c", "sku": SKU, "quantity": True, "unit_price_cents": 100},
        {"customer_id": "c", "sku": SKU, "quantity": 1, "unit_price_cents": -1},
        {"customer_id": "c", "sku": SKU, "quantity": 1, "unit_price_cents": 100, "payment_mode": "cheat"},
    ],
)
def test_invalid_payload_writes_nothing(client, order_database: str, payload) -> None:
    response = post_order(client, "key-invalid", payload)

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_payload"
    assert len(_rows(order_database, "SELECT * FROM orders")) == 0


def test_get_order_returns_the_persisted_state(client) -> None:
    seed(client, 5)
    created = post_order(client, "key-get").get_json()

    response = client.get(f"/orders/{created['order_id']}")

    assert response.status_code == 200
    assert response.get_json()["status"] == "PAID"


def test_get_unknown_order_is_not_found(client) -> None:
    response = client.get("/orders/ord_nope")

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "order_not_found"


def test_state_survives_an_order_api_restart(order_database: str, mock_server) -> None:
    first_app = create_order_app(
        {
            "DATABASE": order_database,
            "PAYMENT_BASE_URL": mock_server["url"],
            "PAYMENT_TIMEOUT_SECONDS": 0.4,
        }
    )
    with first_app.test_client() as first_client:
        seed(first_client, 5)
        created = post_order(first_client, "key-restart").get_json()

    second_app = create_order_app(
        {
            "DATABASE": order_database,
            "PAYMENT_BASE_URL": mock_server["url"],
            "PAYMENT_TIMEOUT_SECONDS": 0.4,
        }
    )
    with second_app.test_client() as second_client:
        response = second_client.get(f"/orders/{created['order_id']}")

    assert response.status_code == 200
    assert response.get_json()["status"] == "PAID"
    assert response.get_json()["reservation"]["status"] == "CONFIRMED"


def test_admin_endpoints_can_be_disabled(order_database: str, mock_server) -> None:
    application = create_order_app(
        {
            "DATABASE": order_database,
            "PAYMENT_BASE_URL": mock_server["url"],
            "ALLOW_ADMIN": False,
        }
    )
    with application.test_client() as client:
        response = client.post(
            "/admin/inventory", json={"sku": SKU, "name": "x", "available": 1}
        )

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "admin_disabled"
