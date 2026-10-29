"""HTTP client for the Payment Mock API.

Every failure to get a trustworthy answer is raised as `PaymentUnavailable`, and
the caller must translate that into an uncertain order state. It must never
assume the charge did not happen.
"""

from __future__ import annotations

from typing import Any

import requests


class PaymentUnavailable(Exception):
    """The mock could not confirm what happened to the charge."""


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


def charge(
    base_url: str,
    key: str,
    order_id: str,
    amount_cents: int,
    currency: str,
    mode: str,
    timeout: float,
) -> dict[str, Any]:
    """Request a charge. Raises `PaymentUnavailable` when the outcome is unknown."""
    payload = {
        "order_id": order_id,
        "amount_cents": amount_cents,
        "currency": currency,
        "mode": mode,
    }
    try:
        response = requests.post(
            f"{_base(base_url)}/payments",
            json=payload,
            headers={"Idempotency-Key": key},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise PaymentUnavailable(str(exc)) from exc

    if response.status_code >= 400:
        raise PaymentUnavailable(f"mock answered HTTP {response.status_code}")
    return response.json()


def lookup(base_url: str, key: str, timeout: float) -> dict[str, Any] | None:
    """Read the persisted payment. `None` means the mock never saw that key."""
    try:
        response = requests.get(f"{_base(base_url)}/payments/{key}", timeout=timeout)
    except requests.RequestException as exc:
        raise PaymentUnavailable(str(exc)) from exc

    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise PaymentUnavailable(f"mock answered HTTP {response.status_code}")
    return response.json()
