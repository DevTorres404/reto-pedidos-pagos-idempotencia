# Fase 03 — Order API: flujo feliz

- **Estado:** cerrada
- **Fecha de cierre:** 2026-10-28
- **Documentos previos relevantes:** `fase-01-diseno-estados-y-esquemas.md`,
  `fase-02-payment-mock.md`

## Objetivo

Crear un pedido por HTTP que reserve stock, cobre contra el Payment Mock y quede en
`PAID`, dejando exactamente una reserva `CONFIRMED` y un cobro `APPROVED`. Consultar
ese pedido después devuelve el estado persistido, no un eco de memoria.

## Alcance

**Incluye**

- `POST /orders`, `GET /orders/<id>`, `GET /inventory`.
- `POST /admin/inventory` y `POST /admin/reset` como herramientas de demo.
- Reserva de stock con `UPDATE` condicional dentro de `BEGIN IMMEDIATE`.
- Cliente HTTP del mock (`order_api/payments.py`).
- `run.ps1` para levantar ambos servicios.

**No incluye**

- Replay por clave (fase 04).
- Estado incierto y reconciliación (fase 05).
- Compensación por rechazo (fase 06).

## Decisiones

| # | Decisión | Alternativa descartada | Motivo |
| --- | --- | --- | --- |
| D1 | Verificar que el SKU existe **antes** de insertar el pedido, dentro de la misma transacción | Dejar que la FK `orders.sku → inventory.sku` falle | La FK convertía un `404 product_not_found` en un `IntegrityError` de 500. Con `BEGIN IMMEDIATE` la lectura y la escritura están serializadas, así que el pre-chequeo es confiable |
| D2 | Reservar con `UPDATE inventory SET available = available - ?, reserved = reserved + ? WHERE sku = ? AND available >= ?` | `SELECT` y después `UPDATE` con el valor calculado en Python | El `WHERE ... available >= ?` es la garantía atómica: si otra transacción se llevó la última unidad, `rowcount` es 0 y no se reserva nada |
| D3 | Agregar `unit_price_cents` y `payment_mode` a `orders` | Recalcular el precio al reconciliar; asumir `approve` al reintentar | El pedido tiene que recordar el escenario pedido. Si el cobro nunca llegó y el pedido era `timeout`, reintentar como `approve` cambiaría la historia |
| D4 | El estado del cobro **no** se duplica en `orders` | Guardar `payment_status` en la tabla de pedidos | Sería una segunda fuente de verdad sobre algo que ya es dueño el mock. La bitácora `order_events` registra lo que respondió el mock, con `detail` |
| D5 | `POST /admin/reset` vacía las tablas de dominio | Solo borrar archivos `.db` a mano | La colección de Postman necesita un punto de partida idéntico en cada corrida, sin reiniciar procesos |
| D6 | `unit_price_cents` sin `DEFAULT` en el esquema | Default `0` | Un precio que se olvida y queda en 0 es un bug silencioso; sin default, el `INSERT` incompleto falla |

### Enmienda al esquema de la fase 01

`orders` sumó dos columnas: `unit_price_cents INTEGER NOT NULL CHECK (>= 0)` y
`payment_mode TEXT NOT NULL DEFAULT 'approve' CHECK (IN ('approve','reject','timeout'))`.
El test de contrato de esquema (`tests/test_schema_contract.py`) se actualizó para
insertarlas explícitamente. No cambia ningún estado: solo agrega datos que el flujo
necesita para ser repetible.

## Contratos resultantes

`POST /orders` (requiere `Idempotency-Key`):

```json
{
  "customer_id": "cust-1",
  "sku": "SKU-1",
  "quantity": 1,
  "unit_price_cents": 9900,
  "currency": "USD",
  "payment_mode": "approve"
}
```

| Código | Cuándo |
| --- | --- |
| `201` | Pedido creado y cobro resuelto (`PAID` o `REJECTED`) |
| `200` | Replay de una clave conocida (fase 04) |
| `202` | Cobro con resultado desconocido (fase 05) |
| `400` | Falta la clave o el payload es inválido |
| `404` | SKU inexistente |
| `409` | Stock insuficiente (`insufficient_stock`) o clave reusada (`idempotency_key_reuse`) |

La respuesta del pedido incluye `order_id`, `status`, datos de la reserva (`status`,
`sku`, `quantity`), `payment_key`, `total_cents` y `events` (la bitácora completa con
`event`, `from`, `to`, `detail`, `at`).

Errores, siempre con la misma forma: `{"error": {"code": ..., "message": ...}}`.

## Implementación

| Archivo | Responsabilidad |
| --- | --- |
| `order_api/__init__.py` | Paquete; documenta que es dueño solo de `orders.db` |
| `order_api/db.py` | Conexión en autocommit, `PRAGMA foreign_keys`, `BEGIN IMMEDIATE` explícito, `initialize()` |
| `order_api/payments.py` | Cliente del mock. Toda duda se traduce en `PaymentUnavailable`, nunca en "no se cobró" |
| `order_api/app.py` | Rutas, validación, reserva, cobro, transiciones y bitácora |
| `order_api/schema.sql` | Esquema (con la enmienda de D3) |
| `run.ps1` | Arranca los dos servicios; `-Reset` borra las bases |
| `tests/test_order_api.py` | Los cinco casos del reto como tests ejecutables |

## Verificación

```text
$ .\.venv\Scripts\python.exe -m pytest -q
..............................................                           [100%]

$ .\.venv\Scripts\python.exe -m pytest --collect-only -q
tests/test_order_api.py: 20
tests/test_payment_mock.py: 18
tests/test_schema_contract.py: 8
```

46 tests en verde (20 corresponden al Order API).

Smoke real con los dos servicios como procesos separados:

```text
=== case 1: happy path ===
  201 PAID CONFIRMED
```

Evidencia en SQLite tras la corrida: 5 pedidos, 4 reservas, 4 cobros; el pedido del
caso 1 quedó `PAID` / `CONFIRMED` con el stock en `available=4`, `reserved=0`.

## Evidencia

Bitácora del pedido del caso 1, leída de `order_events`:

```text
ORDER_CREATED          None -> PENDING_PAYMENT
STOCK_RESERVED         PENDING_PAYMENT -> PENDING_PAYMENT
PAYMENT_APPROVED       PENDING_PAYMENT -> PAID
RESERVATION_CONFIRMED  PAID -> PAID
```

## Deuda técnica y riesgos

- Un pedido admite **una sola línea** de producto. Es un límite de alcance declarado
  en `fase-01`, no un pendiente: agregar líneas obliga a reservar varias filas en la
  misma transacción y a compensarlas todas.
- `POST /admin/reset` y `POST /admin/inventory` no tienen autenticación. Son
  herramientas de demo; `ORDER_ALLOW_ADMIN=false` los apaga.
- La bitácora crece sin política de retención. Irrelevante para el reto.

## Pendientes

- [x] Flujo feliz con reserva y cobro aprobado.
- [x] Consulta de pedido con bitácora.
- [x] Smoke con procesos reales.

## Próximo paso

Fase 04: la misma clave debe devolver el resultado conocido sin crear nada nuevo, y
una clave reusada con otro payload debe fallar con `409`.
