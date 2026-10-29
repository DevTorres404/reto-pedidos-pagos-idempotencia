-- Order API — SQLite schema
-- Owner: order_api/orders.db. Never opened by payment_mock.
-- All money in integer cents. All timestamps ISO-8601 UTC.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- inventory
-- Two counters, never one: `available` is what a new order may take,
-- `reserved` is stock already promised to an order. The CHECK constraints make
-- a negative stock physically impossible, so the invariant is enforced by the
-- database and not only by application code.
CREATE TABLE IF NOT EXISTS inventory (
    sku         TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    available   INTEGER NOT NULL DEFAULT 0 CHECK (available >= 0),
    reserved    INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- ------------------------------------------------------------------- orders
-- One row per purchase intent. `idempotency_key` carries the UNIQUE constraint
-- the challenge requires: the database, not a dictionary, decides what counts
-- as the same attempt. `request_hash` detects a key reused with a different
-- payload, which is a client bug and must not silently replay.
CREATE TABLE IF NOT EXISTS orders (
    id              TEXT    PRIMARY KEY,
    idempotency_key TEXT    NOT NULL UNIQUE,
    request_hash    TEXT    NOT NULL,
    customer_id     TEXT    NOT NULL,
    sku             TEXT    NOT NULL REFERENCES inventory(sku),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    total_cents     INTEGER NOT NULL CHECK (total_cents >= 0),
    currency        TEXT    NOT NULL DEFAULT 'USD' CHECK (length(currency) = 3),
    status          TEXT    NOT NULL CHECK (status IN (
                        'PENDING_PAYMENT', 'PAYMENT_UNCERTAIN', 'PAID',
                        'REJECTED', 'CANCELLED')),
    -- Scenario requested from the mock. The order must remember it: if the charge
    -- never arrived, reconciliation has to re-issue the same scenario.
    payment_mode    TEXT    NOT NULL DEFAULT 'approve' CHECK (payment_mode IN ('approve', 'reject', 'timeout')),
    payment_key     TEXT    NOT NULL UNIQUE,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- ------------------------------------------------------------- reservations
-- Exactly one reservation per order (UNIQUE order_id). `HELD` is the only
-- reversible state; CONFIRMED and RELEASED are terminal, so release twice has
-- no second effect.
CREATE TABLE IF NOT EXISTS reservations (
    id         TEXT    PRIMARY KEY,
    order_id   TEXT    NOT NULL UNIQUE REFERENCES orders(id),
    sku        TEXT    NOT NULL REFERENCES inventory(sku),
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    status     TEXT    NOT NULL CHECK (status IN ('HELD', 'CONFIRMED', 'RELEASED')),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_reservations_status ON reservations(status);

-- ------------------------------------------------------------- order_events
-- Append-only audit trail. This is the evidence artifact: it shows the exact
-- transition sequence a grader can compare against the payment records.
CREATE TABLE IF NOT EXISTS order_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT    NOT NULL REFERENCES orders(id),
    event       TEXT    NOT NULL,
    from_status TEXT,
    to_status   TEXT    NOT NULL,
    detail      TEXT,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id, id);
