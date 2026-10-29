# Fase 04 — Idempotencia

- **Estado:** cerrada
- **Fecha de cierre:** 2026-10-28
- **Documentos previos relevantes:** `fase-01-diseno-estados-y-esquemas.md`,
  `fase-03-order-api-flujo-feliz.md`

## Objetivo

Que repetir exactamente la misma solicitud de compra no cree un segundo pedido, una
segunda reserva ni un segundo cobro, y que reusar una clave con **otro** payload
falle en vez de devolver datos que no corresponden.

## Alcance

**Incluye**

- `UNIQUE (idempotency_key)` en `orders` y en `payments`.
- Replay: `200` con `X-Idempotent-Replay: true` y el resultado ya conocido.
- `409 idempotency_key_reuse` cuando el payload no coincide.
- Huella (`request_hash`) del payload normalizado.
- Lectura previa por clave y respaldo por `IntegrityError` para la carrera real.

**No incluye**

- Resolver un pedido en estado incierto: eso es fase 05.

## Decisiones

| # | Decisión | Alternativa descartada | Motivo |
| --- | --- | --- | --- |
| D1 | La restricción `UNIQUE` es la garantía; no hay diccionario en memoria | Set de claves vistas en el proceso | Un reinicio borra el set y vuelve a cobrar. La base no olvida |
| D2 | Buscar la clave **antes** de insertar | Depender solo del `IntegrityError` | El camino normal de replay no debería pasar por una excepción: es el caso esperado, no un error |
| D3 | Mantener el manejador de `IntegrityError` como respaldo | Eliminarlo, porque ya hay pre-lectura | Dos requests simultáneos con la misma clave pueden leer `None` a la vez; una gana el `INSERT` y la otra tiene que responder el replay, no un 500 |
| D4 | La huella se calcula sobre el payload **normalizado** y canónico | Hashear el cuerpo crudo | El mismo pedido con las claves JSON en otro orden, o `usd` vs `USD`, es la misma intención y no debe dar 409 |
| D5 | Replay devuelve `200`, no `201` | Devolver `201` como si se hubiera creado | El cliente tiene que poder distinguir "lo creé" de "ya existía". El header lo hace explícito |
| D6 | El mock usa el mismo patrón y compara `order_id`, `amount_cents` y `currency` | Comparar solo la clave | Una clave reusada para otro monto es un bug del cliente que debe verse, no taparse |

## Contratos resultantes

```text
POST /orders        Idempotency-Key: <clave>
  ├── clave nueva                          → 201  creación real
  ├── clave conocida, mismo payload        → 200  + X-Idempotent-Replay: true
  ├── clave conocida, otro payload         → 409  idempotency_key_reuse
  └── sin clave                            → 400  missing_idempotency_key

POST /payments      Idempotency-Key: <clave>
  ├── clave nueva                          → 201
  ├── clave conocida, mismo cobro          → 200  + X-Idempotent-Replay: true
  ├── clave conocida, otro monto/moneda    → 409  idempotency_key_reuse
  └── sin clave                            → 400
```

La clave de cobro **no** la elige el cliente: la genera el Order API (`pay_<uuid>`) y
la persiste en el pedido. Así la relación pedido ↔ cobro es 1:1 y verificable en SQL.

## Implementación

| Archivo | Responsabilidad |
| --- | --- |
| `order_api/app.py` | `_request_hash`, `_replay`, pre-lectura por clave, manejador de `IntegrityError` |
| `order_api/schema.sql` | `UNIQUE (idempotency_key)` en `orders` |
| `payment_mock/app.py` | Inserción, replay y `409` por diferencia de contenido |
| `payment_mock/schema.sql` | `UNIQUE (idempotency_key)` en `payments` |
| `tests/test_order_api.py` | Replay, reuso con otro payload, clave faltante |
| `tests/test_payment_mock.py` | Replay del mock y conflicto por monto distinto |

## Verificación

```text
$ .\.venv\Scripts\python.exe -m pytest -q
..............................................                           [100%]
```

Tests que cubren esta fase:

```text
test_replay_with_the_same_key_changes_nothing
test_same_key_with_a_different_payload_is_refused
test_missing_idempotency_key_is_rejected
test_replay_returns_the_stored_payment_and_keeps_one_row        (mock)
test_key_reuse_with_a_different_amount_is_a_conflict            (mock)
test_duplicate_idempotency_key_is_rejected                      (esquema)
test_duplicate_payment_key_is_rejected                          (esquema)
```

Cada test de replay cuenta filas después de la segunda llamada: `orders`,
`reservations` y `payments` quedan en 1, y el inventario no se mueve de nuevo.

Smoke real:

```text
=== case 2: same key replayed ===
  200 replay: true same order: True
```

## Evidencia

Tras el caso 1 + caso 2 en la misma corrida: 1 pedido, 1 reserva, 1 cobro, stock en
`available=4`, `reserved=0`. La segunda llamada no movió nada.

## Deuda técnica y riesgos

- La huella no expira: la clave es válida para siempre. Un sistema real necesitaría
  una ventana de retención (24 h es lo habitual) y un índice por fecha.
- Replay de una clave cuyo pedido está en `PAYMENT_UNCERTAIN` devuelve ese estado
  (`200`), no dispara una reconciliación. Es deliberado: el replay informa, la
  reconciliación es una acción explícita.

## Pendientes

- [x] Replay sin efectos nuevos.
- [x] Conflicto por clave reusada con otro payload.
- [x] Clave obligatoria en ambos servicios.

## Próximo paso

Fase 05: qué pasa cuando el cobro ocurrió pero la respuesta se perdió.
