-- Payment Mock API — SQLite schema
-- Owner: payment_mock/payments.db. Never opened by order_api.
-- Simulates a card processor. No real cards, no real money.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- payments
-- One row per payment attempt key. The row is INSERTed -- with its final status
-- -- BEFORE the HTTP response is written. That ordering is the whole point of
-- the challenge: a timeout means "the client never learned the outcome", never
-- "no charge happened". For mode='timeout', status='CHARGED_TIMEOUT' records a
-- charge that is real and a response that was dropped on purpose.
--
-- `order_id` is a plain value, not a foreign key: each service owns its own
-- database and must not depend on the other one's schema.
CREATE TABLE IF NOT EXISTS payments (
    id              TEXT    PRIMARY KEY,
    idempotency_key TEXT    NOT NULL UNIQUE,
    order_id        TEXT    NOT NULL,
    amount_cents    INTEGER NOT NULL CHECK (amount_cents >= 0),
    currency        TEXT    NOT NULL DEFAULT 'USD' CHECK (length(currency) = 3),
    mode            TEXT    NOT NULL CHECK (mode IN ('approve', 'reject', 'timeout')),
    status          TEXT    NOT NULL CHECK (status IN ('APPROVED', 'REJECTED', 'CHARGED_TIMEOUT')),
    failure_reason  TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
