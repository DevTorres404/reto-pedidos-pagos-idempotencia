# Fase 06 — Compensación por rechazo y stock nunca negativo

- **Estado:** cerrada
- **Fecha de cierre:** 2026-10-28
- **Documentos previos relevantes:** `fase-03-order-api-flujo-feliz.md`,
  `fase-04-idempotencia.md`

## Objetivo

Que un cobro rechazado libere la reserva **una sola vez**, deje el stock como estaba
y nunca permita que una segunda intención de compra se lleve una unidad que ya no
existe.

## Alcance

**Incluye**

- Transición `PENDING_PAYMENT → REJECTED` con compensación de la reserva.
- `reservations.status`: `HELD` → `CONFIRMED` o `RELEASED`.
- `CHECK (available >= 0)` y `CHECK (reserved >= 0)` en el esquema como última red.
- `409 insufficient_stock` con el pedido en `CANCELLED`, sin haber cobrado.
- Reconciliar un pedido `REJECTED` no libera dos veces.

**No incluye**

- Devolución del dinero. El mock nunca cobró: `charged=false`, no hay nada que
  revertir en la pasarela.

## Decisiones

| # | Decisión | Alternativa descartada | Motivo |
| --- | --- | --- | --- |
| D1 | La compensación es un `UPDATE` condicional `WHERE status = 'HELD'` sobre la reserva | `SELECT` del estado y después `UPDATE` | El `rowcount` es la prueba de "esto lo liberé yo". Si es 0, alguien ya lo hizo y no se toca el inventario |
| D2 | El stock vuelve con `available = available + ?` y `reserved = reserved - ?` en el mismo `UPDATE` | Dos `UPDATE` separados | Una sola sentencia mantiene la invariante: o se mueven los dos contadores o ninguno |
| D3 | Si el inventario y la reserva no coinciden, la transacción se revierte y se levanta un error | Continuar y dejar el stock "aproximado" | Un desbalance silencioso es peor que un 500 ruidoso: significa que la reserva y el stock ya no representan la realidad |
| D4 | Un pedido sin stock se crea en `CANCELLED` con el evento `STOCK_REJECTED` | Devolver `409` sin dejar rastro | La intención existió y se rechazó. La bitácora lo prueba; un `409` sin fila no deja evidencia |
| D5 | Un SKU inexistente no crea pedido (`404`, sin fila) | Crearlo `CANCELLED` | No hay intención válida sobre un producto que no existe; además la FK no lo permitiría |
| D6 | `CHECK (reserved >= 0)` en el esquema, además del `WHERE reserved >= ?` | Confiar solo en el código | La base es la última línea de defensa. Si el código se equivoca, la escritura falla en lugar de corromper |

## Contratos resultantes

```text
POST /orders  (stock disponible)
  └── cobro rechazado → 201  status = REJECTED
                             reservation.status = RELEASED
                             inventory: available +1, reserved -1

POST /orders  (stock insuficiente)
  └── 409  insufficient_stock
           pedido CANCELLED con evento STOCK_REJECTED
           sin reserva, sin cobro, stock sin cambios

POST /orders  (SKU inexistente)
  └── 404  product_not_found   (no se escribe nada)
```

Secuencia de eventos de un rechazo:

```text
ORDER_CREATED         None -> PENDING_PAYMENT
STOCK_RESERVED        PENDING_PAYMENT -> PENDING_PAYMENT
PAYMENT_REJECTED      PENDING_PAYMENT -> REJECTED    (detail.payment_status = "REJECTED")
RESERVATION_RELEASED  REJECTED -> REJECTED
```

## Implementación

| Archivo | Responsabilidad |
| --- | --- |
| `order_api/app.py` | `_settle` unifica confirmación y compensación bajo la misma guarda de estado |
| `order_api/schema.sql` | `CHECK (available >= 0)`, `CHECK (reserved >= 0)`, `UNIQUE (order_id)` en `reservations` |
| `tests/test_order_api.py` | Rechazo, doble reconciliación, última unidad, SKU inexistente |

## Verificación

```text
$ .\.venv\Scripts\python.exe -m pytest -q
..............................................                           [100%]
```

Tests que cubren esta fase:

```text
test_rejected_payment_releases_the_reservation_exactly_once
test_last_unit_goes_to_one_intent_and_stock_never_goes_negative
test_unknown_product_is_not_found
test_negative_stock_is_impossible                         (esquema)
test_second_reservation_for_same_order_is_rejected        (esquema)
```

`test_rejected_payment_releases_the_reservation_exactly_once` cuenta además los
eventos `RESERVATION_RELEASED`: exactamente 1 después del rechazo y de una
reconciliación posterior.

Smoke con procesos separados:

```text
=== case 4: rejected payment ===
  201 REJECTED RELEASED stock: {'available': 5, 'reserved': 0}

=== case 5: last unit, different key ===
  A: 201 PAID
  B: 409 insufficient_stock
  stock: {'available': 0, 'reserved': 0}
```

## Evidencia

Estado final de la corrida completa, leído de SQLite:

```text
case4      REJECTED   RELEASED
case5-a    PAID       CONFIRMED
case5-b    CANCELLED  (sin reserva)
```

5 pedidos, 4 reservas, 4 cobros. La segunda intención del caso 5 nunca llegó a
cobrar: el stock se rechazó antes de hablar con el mock.

## Deuda técnica y riesgos

- Si el proceso muere entre el cobro rechazado y la compensación, el pedido queda en
  `PENDING_PAYMENT` con la reserva tomada. La reconciliación lo resuelve (fase 05):
  consulta el cobro `REJECTED` y compensa. No se perdió la unidad, pero hace falta
  que alguien reconcilie.
- La compensación es por pedido completo. Con varias líneas habría que compensar cada
  reserva; el alcance actual es una línea (ver `fase-01`).

## Pendientes

- [x] Rechazo libera la reserva exactamente una vez.
- [x] Stock nunca negativo, ni por carrera ni por doble liberación.
- [x] Intención sin stock queda registrada como `CANCELLED`.

## Próximo paso

Fase 07: convertir todo esto en una colección de Postman reproducible y en consultas
SQL que sirvan como evidencia para la entrega.
