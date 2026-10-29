# Fase 01 — Diseño de estados, esquemas SQL y contratos HTTP

- **Estado:** cerrada
- **Fecha de cierre:** 2026-10-28
- **Documentos previos relevantes:** `fase-00-bootstrap.md`

## Objetivo

Fijar, antes de escribir lógica de negocio, tres cosas que las fases 02 a 06 no
pueden renegociar: la máquina de estados, las restricciones que viven en SQLite y la
superficie HTTP con sus códigos de respuesta.

## Alcance

**Incluye**

- DDL de `order_api/schema.sql` y `payment_mock/schema.sql`.
- Contrato HTTP completo de ambos servicios.
- Secuencia paso a paso de los cinco casos obligatorios.
- Tests de contrato del esquema (restricciones, no endpoints).

**No incluye**

- Código Flask, capa de base de datos, cliente HTTP entre servicios.
- Colección Postman.

## Decisiones

| # | Decisión | Alternativa descartada | Motivo |
| --- | --- | --- | --- |
| D1 | La clave de idempotencia viaja en el header `Idempotency-Key`, obligatorio | Campo en el body, o ambos | Un solo lugar elimina la ambigüedad de "cuál gana" y hace visible en Postman que la clave es del protocolo, no del pedido |
| D2 | `orders.request_hash` = SHA-256 del payload canónico; si la misma clave llega con otro hash → `409 idempotency_key_reuse` | Replay silencioso | Reusar una clave con otro contenido es un bug del cliente; devolver el pedido anterior ocultaría el error |
| D3 | Primera creación responde **201**; replay responde **200** con header `X-Idempotent-Replay: true` | Mismo código en ambos casos | El código de estado es la evidencia que se ve en la captura de Postman, sin abrir la base |
| D4 | Un pedido = una línea (`sku` + `quantity`) | Tabla `order_items` multi-línea | El enunciado evalúa idempotencia y recuperación, no multiplicidad; una reserva por pedido hace verificable "un pedido, una reserva, un cobro" |
| D5 | `payment_key` = `pay_<order_id>`, derivada y guardada con `UNIQUE` | UUID nuevo por intento | Una clave estable hace que el mock sea idempotente incluso si el Order API reintenta, que es justo lo que se quiere demostrar |
| D6 | Inventario con dos contadores: `available` y `reserved`, ambos con `CHECK (>= 0)` | Un solo contador `stock` | Separa lo libre de lo comprometido: confirmar y liberar son operaciones distintas y ambas deben poder auditarse. El `CHECK` hace imposible el stock negativo aunque la aplicación tenga un bug |
| D7 | `reservations.order_id` es `UNIQUE` | Permitir varias reservas por pedido | "Una reserva" deja de ser una convención y pasa a ser una restricción |
| D8 | `order_events` append-only con `from_status`/`to_status` | Sin bitácora | Es la evidencia que se pega en el PDF: la secuencia exacta de transiciones, no una interpretación |
| D9 | En el mock, `status` se decide y se inserta **antes** de responder; `timeout` persiste `CHARGED_TIMEOUT` | Insertar después de responder | Es el corazón del reto: el cobro existe aunque el cliente no se haya enterado |
| D10 | El mock distingue `mode` (escenario pedido) de `status` (resultado persistido) | Un solo campo | Se puede probar que el escenario pedido fue `timeout` y el resultado fue un cobro real |
| D11 | Pago incierto → el Order API responde **202** con `PAYMENT_UNCERTAIN` | 500 o 200 | 202 comunica "aceptado, resultado pendiente", que es exactamente la naturaleza del estado |
| D12 | Si la reconciliación recibe 404 del mock, se permite reenviar el cobro con la misma clave | Fallar y dejar el pedido incierto | 404 significa "nunca llegó"; el `UNIQUE` del mock garantiza que reenviar no puede duplicar. Sin esa restricción, reenviar sería peligroso |
| D13 | Liberar una reserva se hace con `UPDATE ... WHERE status = 'HELD'` y se verifica `rowcount` | Leer estado y luego escribir | La transición condicional hace que la segunda compensación sea un no-op sin locks |
| D14 | Inventario sembrado por `POST /admin/inventory` + visible en `GET /inventory` | Script de seed o SQL manual | Permite que el caso 5 ("queda una unidad") sea reproducible desde Postman con una sola request |

## Contratos HTTP

Formato de error único en ambos servicios:

```json
{"error": {"code": "insufficient_stock", "message": "SKU-1 has 0 units available"}}
```

### Order API — puerto 5000

| Método | Ruta | Entrada | Salidas |
| --- | --- | --- | --- |
| `POST` | `/orders` | Header `Idempotency-Key`; body `{customer_id, sku, quantity, unit_price_cents, currency?, payment_mode?}` | `201` creado · `200` replay · `202` incierto · `400` clave o payload inválido · `404` producto inexistente · `409` sin stock o clave reusada |
| `GET` | `/orders/<order_id>` | — | `200` pedido con reserva y último evento · `404` |
| `POST` | `/orders/<order_id>/reconcile` | — | `200` estado final · `202` sigue incierto · `404` pedido inexistente · `409` transición no permitida · `503` mock inalcanzable |
| `GET` | `/inventory` | — | `200` lista de SKUs con `available` y `reserved` |
| `POST` | `/admin/inventory` | `{sku, name, available}` (upsert) | `200` estado del SKU |
| `GET` | `/health` | — | `200` |

`payment_mode` (`approve` \| `reject` \| `timeout`) es una ayuda de demostración que
el Order API reenvía al mock. Sin él, el mock usa `approve`.

Cuerpo de respuesta de un pedido:

```json
{
  "order_id": "…", "status": "PAID",
  "idempotency_key": "…", "customer_id": "…",
  "sku": "SKU-1", "quantity": 1, "total_cents": 9900, "currency": "USD",
  "payment": {"key": "pay_…", "status": "APPROVED"},
  "reservation": {"id": "…", "status": "CONFIRMED"},
  "created_at": "…", "updated_at": "…"
}
```

### Payment Mock API — puerto 5001

| Método | Ruta | Entrada | Salidas |
| --- | --- | --- | --- |
| `POST` | `/payments` | Header `Idempotency-Key`; body `{order_id, amount_cents, currency?, mode?}` | `201` cobro creado · `200` replay · `400` inválido · `409` clave reusada con otro monto |
| `GET` | `/payments/<idempotency_key>` | — | `200` pago persistido · `404` nunca existió |
| `GET` | `/health` | — | `200` |

`mode=timeout` persiste `CHARGED_TIMEOUT`, duerme `MOCK_TIMEOUT_DELAY` segundos y
recién entonces responde `201`. El cliente corta antes por timeout de lectura: la
respuesta se pierde, el cobro queda.

## Secuencia de los cinco casos

| Caso | Secuencia esperada | Evidencia en SQLite |
| --- | --- | --- |
| 1. Flujo exitoso | `POST /orders` → reserva `HELD` → `POST /payments` `APPROVED` → pedido `PAID`, reserva `CONFIRMED` | 1 order, 1 reservation `CONFIRMED`, 1 payment `APPROVED` |
| 2. Misma clave | Repetir el `POST` con la misma clave | Mismos `COUNT(*)`; respuesta `200` con `X-Idempotent-Replay: true` |
| 3. Respuesta perdida | `payment_mode=timeout` → pedido `PAYMENT_UNCERTAIN`, 202 → `POST /reconcile` → `GET /payments/pay_…` en el mock devuelve `CHARGED_TIMEOUT` → pedido `PAID` | 1 payment `CHARGED_TIMEOUT`, 1 order `PAID`, `order_events` con `PAYMENT_INITIATED → PAYMENT_UNCERTAIN → RECONCILED_PAID` |
| 4. Rechazo | `payment_mode=reject` → `REJECTED`, reserva `RELEASED`, stock devuelto | 1 reservation `RELEASED`, `inventory.available` vuelve al valor inicial, 1 payment `REJECTED` |
| 5. Otra intención, última unidad | `POST /admin/inventory` deja `available=1`; pedido A con clave A toma la unidad; pedido B con clave B falla con `409 insufficient_stock` | 2 orders (una `PAID`/`PENDING`, otra `CANCELLED`), `available=0`, nunca negativo |

## Esquema resultante

`order_api/schema.sql`

| Tabla | Restricción que importa |
| --- | --- |
| `inventory` | `CHECK (available >= 0)`, `CHECK (reserved >= 0)` |
| `orders` | `UNIQUE (idempotency_key)`, `UNIQUE (payment_key)`, `CHECK (status IN …)` |
| `reservations` | `UNIQUE (order_id)`, `CHECK (status IN ('HELD','CONFIRMED','RELEASED'))` |
| `order_events` | Append-only; `detail` guarda JSON con el motivo |

`payment_mock/schema.sql`

| Tabla | Restricción que importa |
| --- | --- |
| `payments` | `UNIQUE (idempotency_key)`, `CHECK (mode IN …)`, `CHECK (status IN …)` |

`order_id` en `payments` es un valor plano, no una FK: cada servicio es dueño de su
base y no conoce el esquema del otro.

## Verificación

```text
$ .\.venv\Scripts\python.exe -m pytest -q
........                                                                 [100%]
8 passed in 0.05s
```

Los ocho tests cubren: creación de todas las tablas, defaults de timestamp, clave de
idempotencia duplicada, clave de pago duplicada, stock negativo imposible, segunda
reserva para el mismo pedido, estado desconocido y modo/estado de pago inválidos.

Un test falló primero y se corrigió: `test_unknown_status_is_rejected` intentaba
disparar un `IntegrityError` con un `UPDATE` que no afectaba filas, y SQLite no
valida `CHECK` sobre filas que no toca. La versión final inserta el pedido antes de
forzar el estado inválido.

## Evidencia

- `tests/test_schema_contract.py` — 8 tests en verde.
- Restricciones legibles en los dos `schema.sql` con el porqué escrito al lado.

## Deuda técnica y riesgos

- Un pedido soporta una sola línea de producto. Si el alcance cambia, hace falta
  `order_items` y la reserva pasa a ser por línea.
- Moneda única (`USD` por defecto). No hay conversión ni validación de monedas
  distintas entre pedido y pago más allá de la longitud del código.
- SQLite y concurrencia: hace falta `busy_timeout` y `BEGIN IMMEDIATE` en las
  escrituras para que dos requests simultáneas no se pisen (se implementa en fase 03).
- El timeout simulado usa una espera real del mock. Es determinista pero suma
  segundos a la suite; se configura por variable de entorno.

## Pendientes

- [ ] Commit de la fase (esquemas + tests + este documento).
- [ ] Definir precio: el body trae `unit_price_cents`, así que el total es
      `unit_price_cents * quantity`. Confirmar que no debe salir del inventario.

## Próximo paso

Fase 02: Payment Mock con `POST /payments`, `GET /payments/<key>`, persistencia
antes de responder y el escenario `timeout`. Es la pieza que la fase 05 va a
necesitar; conviene tenerla probada antes de tocar el Order API.
