# AGENTS.md — retotecnico

Two local Flask services that demonstrate idempotent order creation, a simulated
payment that loses its response, and state recovery backed by SQLite.

Read `reto_tecnico_pedidos_pagos_idempotencia.md` (Spanish, the assignment) for the
full grading contract. Load the `reto-pedidos-pagos` skill before touching order or
payment state.

## Services

| Service | Port | Owns | Purpose |
| --- | --- | --- | --- |
| `order_api` | 5000 | `order_api/orders.db` | Orders, inventory, reservations, idempotency keys, reconciliation |
| `payment_mock` | 5001 | `payment_mock/payments.db` | Simulated charges, charge states, lookup by key |

Each service owns its database. Cross-service access is a defect.

## Target layout

```
order_api/        Flask app, db layer, order state machine
payment_mock/     Flask app, simulated charge state
tests/            pytest suite (per service + end-to-end)
postman/          exported collection + environment
doc/              per-phase documentation (index: doc/README.md)
run.ps1           starts both services for local demo
```

## Commands

```powershell
.venv\Scripts\Activate.ps1
python -m pytest -q              # full suite
python -m pytest tests -k unit   # unit only
.\run.ps1                        # both services, two terminals
python -m order_api.app          # single service

# verification of the delivery artifacts
newman run postman\reto-pedidos-pagos.postman_collection.json -e postman\local.postman_environment.json
python postman\run_evidence.py   # no sqlite3 CLI required
```

Databases are created and migrated on startup; delete `*.db` to reset state.

## Conventions

- Python 3.13, Flask, stdlib `sqlite3`. No ORM, no external queue, no Redis.
- `sqlite3` connections per request with `foreign_keys=ON`; wrap multi-statement
  writes in an explicit transaction.
- Idempotency and stock correctness rely on `UNIQUE` constraints and conditional
  `UPDATE`s, never on in-process locks or dictionaries.
- Money in integer cents. Never floats.
- Errors return a stable JSON shape: `{"error": {"code": ..., "message": ...}}`.
- Tests exercise the HTTP surface, not private functions, and assert on persisted
  row counts after the request.

## Artifact language

- Code, identifiers, comments, tests, commit messages: English.
- `README.md`, delivery PDF, README run instructions: Spanish (the evaluator's
  language).

## Quality gates before claiming work done

1. `python -m pytest -q` green.
2. The five Postman cases still pass against a restarted pair of services.
3. Row counts per idempotency key unchanged on replay (`orders`, `reservations`,
   `payments`).
4. Restart both services and confirm the state survives.

## Phase documentation

Development runs in phases. Each phase closes with one document in `doc/` named
`fase-NN-<slug>.md`, built from `doc/_plantilla-fase.md` and registered in the index
at `doc/README.md`. A phase is not closed until that document exists with observed
verification evidence, not inferred.

## Skills

Load from `.atl/skill-registry.md`. `reto-pedidos-pagos` is mandatory for domain
work; `work-unit-commits` for commit shaping; `chained-pr` only if a change exceeds
the review budget.
