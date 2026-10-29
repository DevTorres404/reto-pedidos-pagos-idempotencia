-- Evidence queries for the delivery PDF.
--
-- Run from the repository root, after a demo session:
--
--   .\.venv\Scripts\python.exe postman\run_evidence.py
--
-- or, if the sqlite3 CLI is installed:
--
--   sqlite3 -header -column order_api/orders.db ".read postman/evidence.sql"
--
-- This file is plain SQL: no dot-commands, so both runners work.
--
-- The ATTACH is only for this evidence script: the services themselves never open
-- each other's database.

ATTACH DATABASE 'payment_mock/payments.db' AS pay;

-- 1. How many effects exist, in total.
SELECT 'orders'       AS table_name, COUNT(*) AS rows FROM orders
UNION ALL SELECT 'reservations', COUNT(*) FROM reservations
UNION ALL SELECT 'payments',     COUNT(*) FROM pay.payments;

-- 2. Every key must appear at most once in orders. Zero rows here is the proof.
SELECT idempotency_key, COUNT(*) AS occurrences
FROM orders
GROUP BY idempotency_key
HAVING COUNT(*) > 1;

-- 3. Every order must have at most one reservation. Zero rows here is the proof.
SELECT order_id, COUNT(*) AS reservations
FROM reservations
GROUP BY order_id
HAVING COUNT(*) > 1;

-- 4. Every payment key must appear at most once. Zero rows here is the proof.
SELECT idempotency_key, COUNT(*) AS occurrences
FROM pay.payments
GROUP BY idempotency_key
HAVING COUNT(*) > 1;

-- 5. The state each key ended in, with its reservation and its charge.
SELECT o.idempotency_key,
       o.status          AS order_status,
       r.status          AS reservation_status,
       p.status          AS payment_status,
       o.total_cents
FROM orders o
LEFT JOIN reservations r ON r.order_id = o.id
LEFT JOIN pay.payments p ON p.idempotency_key = o.payment_key
ORDER BY o.created_at;

-- 6. The evidence of case 3: the charge exists even though the response was lost,
--    and the order still ended up PAID because of it.
SELECT p.idempotency_key, p.order_id, p.mode, p.status AS payment_status, o.status AS order_status
FROM pay.payments p
JOIN orders o ON o.payment_key = p.idempotency_key
WHERE p.status = 'CHARGED_TIMEOUT';

-- 7. The transition trail. This is what shows the recovery, not just the result.
SELECT o.idempotency_key, e.event, e.from_status, e.to_status, e.detail
FROM order_events e
JOIN orders o ON o.id = e.order_id
ORDER BY o.idempotency_key, e.id;

-- 8. Stock is never negative, and nothing stays reserved at the end of a demo.
SELECT sku, available, reserved,
       CASE WHEN available < 0 OR reserved < 0 THEN 'BROKEN' ELSE 'ok' END AS check_result
FROM inventory;
