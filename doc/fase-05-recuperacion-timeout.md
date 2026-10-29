# Fase 05 — Recuperación cuando la respuesta del cobro se pierde

- **Estado:** cerrada
- **Fecha de cierre:** 2026-10-28
- **Documentos previos relevantes:** `fase-02-payment-mock.md`, `fase-04-idempotencia.md`

## Objetivo

Que un timeout al cobrar no se confunda con "no se cobró". El pedido queda en
`PAYMENT_UNCERTAIN`, y una reconciliación explícita lo resuelve consultando el cobro
real, sin volver a cobrar a ciegas.

## Alcance

**Incluye**

- Estado `PAYMENT_UNCERTAIN` y respuesta `202` con `reconcile_url`.
- `POST /orders/<id>/reconcile`: consulta `GET /payments/<key>`, y solo reintenta si
  el mock responde `404`.
- Transiciones guardadas por estado: reconciliar dos veces no cobra ni confirma dos veces.
- Bitácora del camino incierto (`PAYMENT_UNKNOWN` → `PAYMENT_APPROVED`).

**No incluye**

- Reconciliación automática por scheduler. En el reto es una acción explícita; un
  sistema real la correría desde un job con las mismas garantías.

## Decisiones

| # | Decisión | Alternativa descartada | Motivo |
| --- | --- | --- | --- |
| D1 | Cualquier falla del cliente HTTP (`timeout`, conexión rechazada, 5xx) deja el pedido en `PAYMENT_UNCERTAIN` | Reintentar en el acto | Reintentar sin saber si el primer intento llegó es exactamente cómo se cobra dos veces |
| D2 | La reconciliación **lee primero** y reintenta solo con `404` | Reintentar siempre con la misma clave | Si el cobro existe, reintentar no es necesario; si no existe, la clave única del mock hace que el reintento siga siendo un solo cobro |
| D3 | `202` distingue "no sé" de "listo" | Devolver `201` con estado incierto | El cliente HTTP tiene que poder reaccionar distinto: `201` es final, `202` pide seguimiento |
| D4 | Un pedido ya terminal responde `200` sin tocar nada | Re-ejecutar la transición | Idempotencia por estado: si ya está resuelto, reconciliar no es un error, es un no-op informado |
| D5 | El `reconcile` es `POST` | `GET /orders/<id>?reconcile=true` | Tiene efectos (mueve stock, cierra el pedido). No se disfraza de lectura |
| D6 | Si la reconciliación tampoco obtiene respuesta, el pedido **sigue** en `PAYMENT_UNCERTAIN` | Marcarlo `REJECTED` por descarte | Un cobro incierto no es un cobro rechazado. Se puede reintentar más tarde |
| D7 | `PAYMENT_TIMEOUT_SECONDS` debe ser menor que `MOCK_TIMEOUT_DELAY` | Un único valor compartido | Es el par de perillas que hace reproducible el caso 3. Documentado en `README.md` |

## Contratos resultantes

```text
POST /orders  (payment_mode = "timeout")
  └── 202  status = PAYMENT_UNCERTAIN
          reconcile_url = "/orders/<id>/reconcile"

POST /orders/<id>/reconcile
  ├── cobro encontrado        → 200  estado final (PAID o REJECTED)
  ├── cobro inexistente (404) → se reintenta el mismo escenario, con la misma clave
  ├── el mock no responde     → 202  sigue PAYMENT_UNCERTAIN
  └── pedido ya terminal      → 200  estado final, sin efectos
```

Secuencia de eventos de un pedido recuperado:

```text
ORDER_CREATED          None -> PENDING_PAYMENT
STOCK_RESERVED         PENDING_PAYMENT -> PENDING_PAYMENT
PAYMENT_UNKNOWN        PENDING_PAYMENT -> PAYMENT_UNCERTAIN
PAYMENT_APPROVED       PAYMENT_UNCERTAIN -> PAID      (detail.source = "reconcile")
RESERVATION_CONFIRMED  PAID -> PAID
```

## Implementación

| Archivo | Responsabilidad |
| --- | --- |
| `payment_mock/app.py` | Persiste el cobro, duerme `MOCK_TIMEOUT_DELAY` y recién después responde |
| `order_api/payments.py` | `charge()` y `lookup()`; ambos elevan `PaymentUnavailable` ante cualquier duda |
| `order_api/app.py` | `_mark_uncertain`, `_settle`, `POST /orders/<id>/reconcile` |
| `tests/test_order_api.py` | Mock real en un hilo con `MOCK_TIMEOUT_DELAY=1.5` y timeout de cliente `0.4` |

## Verificación

```text
$ .\.venv\Scripts\python.exe -m pytest -q
..............................................                           [100%]
```

Tests que cubren esta fase:

```text
test_lost_response_is_uncertain_and_reconciles_without_a_second_charge
test_reconcile_is_idempotent_on_a_paid_order
test_reconcile_reissues_when_the_charge_never_reached_the_mock
test_timeout_mode_is_charged_even_though_the_caller_never_learns_it   (mock)
test_slow_mode_persists_the_charge_before_the_response_is_sent        (mock)
```

El mock se levanta como servidor HTTP real (`make_server`) en un hilo aparte: el
timeout del cliente es un timeout de socket de verdad, no una excepción inyectada.

Smoke con procesos separados (`PAYMENT_TIMEOUT_SECONDS=1.0`,
`MOCK_TIMEOUT_DELAY=2.0`):

```text
=== case 3: response lost, then reconcile ===
  202 PAYMENT_UNCERTAIN
  reconcile: 200 PAID
```

## Evidencia

El cobro existe aunque el cliente nunca haya visto la respuesta:

```sql
SELECT idempotency_key, order_id, mode, status
FROM payments
WHERE status = 'CHARGED_TIMEOUT';
```

Y el pedido quedó `PAID` con una sola fila en `payments`: 4 pedidos cobrados en la
corrida completa, 4 cobros. La reconciliación no agregó un segundo registro.

## Deuda técnica y riesgos

- Si el cobro nunca llegó y el escenario era `timeout`, el reintento vuelve a dormir y
  a fallar por timeout. El pedido queda otra vez incierto. Es honesto (no se inventó
  un resultado) pero en producción convendría un contador de intentos con backoff.
- No hay job automático: si nadie llama a `reconcile`, el pedido queda incierto para
  siempre. Es una decisión de alcance, no un olvido.
- El stock reservado de un pedido incierto queda tomado hasta que se reconcilia. Es
  lo correcto (el cobro puede haber pasado), pero exige operación: alguien debe
  mirar los pedidos inciertos.

## Pendientes

- [x] Estado incierto sin asumir el resultado.
- [x] Reconciliación que consulta antes de reintentar.
- [x] Reconciliar dos veces no duplica el cobro ni la confirmación.

## Próximo paso

Fase 06: el camino opuesto — el cobro se rechaza y hay que devolver la reserva
exactamente una vez.
