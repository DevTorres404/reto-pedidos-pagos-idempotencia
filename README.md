# Reto técnico — Pedidos, pagos simulados e idempotencia

Dos servicios locales en Flask + SQLite que demuestran, de punta a punta, que una
operación de compra puede repetirse, perder la respuesta del cobro o ser rechazada
**sin duplicar pedidos, reservas de stock ni cargos**.

- **Order API** (`:5000`) — pedidos, inventario, reservas, claves de idempotencia y
  reconciliación.
- **Payment Mock** (`:5001`) — cobro simulado que puede aprobar, rechazar o quedarse
  con la respuesta sin devolverla.

Cada servicio tiene su propia base de datos y no conoce la del otro. La comunicación
entre ellos es HTTP.

---

## 1. Arquitectura

```text
                    ┌──────────────────────────────────────────┐
   cliente HTTP     │              Order API  :5000            │
  (Postman, curl)   │  orders.db                               │
        │           │  ├── inventory    (available / reserved) │
        └──────────►│  ├── orders       (estado + idempotencia)│
                    │  ├── reservations (HELD/CONFIRMED/…)     │
                    │  └── order_events (bitácora de estados)  │
                    └───────────────┬──────────────────────────┘
                                    │ HTTP  POST /payments
                                    │       GET  /payments/<key>
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │            Payment Mock  :5001           │
                    │  payments.db                             │
                    │  └── payments (UNIQUE idempotency_key)   │
                    └──────────────────────────────────────────┘
```

La garantía de "una sola vez" **no vive en memoria del proceso**: vive en
restricciones `UNIQUE` y en `UPDATE` condicionales dentro de SQLite. Reiniciar los
servicios no rompe nada; dos procesos concurrentes tampoco.

### Por qué el mock persiste antes de responder

`POST /payments` inserta la fila con su estado final y **después** responde. En modo
`timeout` duerme más que el timeout del cliente. Consecuencia: cuando el Order API
recibe un timeout, el cobro **ya existe**. Por eso el pedido pasa a
`PAYMENT_UNCERTAIN` en lugar de asumir que no se cobró.

---

## 2. Requisitos

- Python 3.13
- PowerShell (los comandos de abajo son para Windows)
- Opcional para verificar la entrega: **Postman** o **newman** (Node), y el CLI
  **`sqlite3`** para correr las consultas de evidencia desde la consola. Las
  consultas también se pueden correr sin el CLI con `postman/run_evidence.py`.

## 3. Instalación

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 4. Ejecución

```powershell
.\run.ps1            # abre los dos servicios en ventanas separadas
.\run.ps1 -Reset     # borra las bases y arranca de cero
```

Verificación rápida:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/health
Invoke-RestMethod http://127.0.0.1:5001/health
```

También se pueden levantar por separado:

```powershell
python -m payment_mock.app
python -m order_api.app
```

Las bases se crean y migran solas al arrancar. **El repositorio no incluye bases**:
son artefactos de ejecución, ignorados por git. Para volver a un estado limpio hay
dos caminos equivalentes:

```powershell
.\run.ps1 -Reset     # borra las bases y arranca los dos servicios
```

o, con los servicios detenidos, borrar `order_api/orders.db*` y
`payment_mock/payments.db*` y volver a arrancar.

---

## 5. API

### Order API — `http://127.0.0.1:5000`

| Método | Ruta | Para qué |
| --- | --- | --- |
| `GET` | `/health` | Estado del servicio |
| `POST` | `/orders` | Crear pedido (requiere `Idempotency-Key`) |
| `GET` | `/orders/<order_id>` | Consultar pedido, reserva y bitácora |
| `POST` | `/orders/<order_id>/reconcile` | Resolver un pedido en estado incierto |
| `GET` | `/inventory` | Stock disponible y reservado |
| `POST` | `/admin/inventory` | Sembrar/ajustar stock (demo) |
| `POST` | `/admin/reset` | Vaciar el sistema para repetir la demo |

`POST /orders` recibe:

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

`payment_mode` es el escenario que se le pide al mock: `approve`, `reject` o
`timeout`. El pedido lo guarda, porque si el cobro nunca llegó hay que poder
reintentar **el mismo escenario**.

Respuestas:

| Código | Cuándo | Cuerpo |
| --- | --- | --- |
| `201` | Pedido creado y cobro resuelto | Pedido con `status` final |
| `200` | Replay de la misma clave | El pedido ya conocido + `X-Idempotent-Replay: true` |
| `202` | Cobro con resultado desconocido | Pedido `PAYMENT_UNCERTAIN` + `reconcile_url` |
| `400` | Falta `Idempotency-Key` o payload inválido | `{"error": {"code", "message"}}` |
| `404` | SKU inexistente / pedido inexistente | idem |
| `409` | Stock insuficiente, o clave reusada con otro payload | idem |

### Payment Mock — `http://127.0.0.1:5001`

| Método | Ruta | Para qué |
| --- | --- | --- |
| `GET` | `/health` | Estado del servicio |
| `POST` | `/payments` | Cobrar (requiere `Idempotency-Key`) |
| `GET` | `/payments/<key>` | Consultar un cobro por clave |
| `POST` | `/admin/reset` | Borrar cobros para repetir la demo |

Estados de un cobro: `APPROVED`, `REJECTED`, `CHARGED_TIMEOUT`. El campo `charged`
dice si el dinero se movió, independientemente de si el cliente recibió la respuesta.

### Estados de un pedido

```text
PENDING_PAYMENT ──► PAID
        │           ▲
        ├──────────►└── PAYMENT_UNCERTAIN ──► PAID
        │                                 └─► REJECTED
        ├──► REJECTED     (cobro rechazado → reserva liberada)
        └──► CANCELLED    (stock insuficiente, nunca se cobró)
```

---

## 6. Los cinco casos del reto

La colección `postman/reto-pedidos-pagos.postman_collection.json` contiene una
carpeta por caso. Cada carpeta reinicia ambos servicios y siembra el inventario
antes de empezar, así que se puede correr la colección completa **o una carpeta
suelta** y el resultado es el mismo.

| # | Caso | Qué se dispara | Resultado esperado |
| --- | --- | --- | --- |
| 1 | Flujo exitoso | `POST /orders` (`approve`) | `201` / `PAID`; reserva `CONFIRMED`; 1 pedido, 1 reserva, 1 cobro |
| 2 | Misma solicitud repetida | Mismo `Idempotency-Key` | `200` + `X-Idempotent-Replay: true`; **sin** efectos nuevos |
| 3 | Respuesta perdida | `payment_mode: "timeout"` → `reconcile` | `202` → `PAYMENT_UNCERTAIN`; el cobro existe; reconcile deja `PAID` sin segundo cobro |
| 4 | Pago rechazado | `payment_mode: "reject"` | `REJECTED`; reserva `RELEASED` una sola vez; stock vuelve a 5 |
| 5 | Otra intención de compra | Segunda clave con la última unidad | Primera `201`/`PAID`; segunda `409 insufficient_stock`; stock `0`, nunca negativo |

### Cómo correrla

Con los dos servicios levantados:

```powershell
# Postman: importar la colección y el environment, y usar "Run collection"
# o por consola, si hay newman instalado:
newman run postman/reto-pedidos-pagos.postman_collection.json `
           -e postman/local.postman_environment.json
```

El environment (`postman/local.postman_environment.json`) solo define las bases URL
y el SKU de demo.

---

## 7. Evidencia en SQLite

Al terminar una corrida, las consultas de `postman/evidence.sql` muestran que no hay
duplicados. Desde la raíz del repositorio:

```powershell
.\.venv\Scripts\python.exe postman\run_evidence.py
```

(o, si está instalado el CLI de SQLite:
`sqlite3 -header -column order_api/orders.db ".read postman/evidence.sql"`)

Qué demuestra cada consulta:

1. Conteo total de pedidos, reservas y cobros.
2. Ninguna clave de idempotencia repetida en `orders` (0 filas = correcto).
3. Ningún pedido con más de una reserva (0 filas = correcto).
4. Ninguna clave de cobro repetida en `payments` (0 filas = correcto).
5. Estado final de cada clave, con su reserva y su cobro.
6. El cobro de `CHARGED_TIMEOUT` del caso 3 y el pedido que quedó `PAID`.
7. La bitácora de transiciones, que es lo que muestra la recuperación.
8. Stock nunca negativo.

Ejemplo de resultado de una corrida completa de los cinco casos:

```text
idempotency_key  order_status  reservation_status  payment_status
case1            PAID          CONFIRMED           APPROVED
case3            PAID          CONFIRMED           CHARGED_TIMEOUT
case4            REJECTED      RELEASED            REJECTED
case5-a          PAID          CONFIRMED           APPROVED
case5-b          CANCELLED     NULL                NULL
```

5 pedidos, 4 reservas y 4 cobros: la repetición del caso 2 no creó nada, y la segunda
intención del caso 5 nunca llegó a cobrar porque el stock se rechazó antes.

---

## 8. Variables de entorno

| Variable | Servicio | Default | Para qué |
| --- | --- | --- | --- |
| `ORDER_DB` | Order API | `order_api/orders.db` | Ruta de la base |
| `ORDER_PORT` | Order API | `5000` | Puerto |
| `PAYMENT_BASE_URL` | Order API | `http://127.0.0.1:5001` | Dónde está el mock |
| `PAYMENT_TIMEOUT_SECONDS` | Order API | `1.0` | Timeout de lectura contra el mock |
| `ORDER_ALLOW_ADMIN` | Order API | `true` | Habilita `/admin/*` |
| `PAYMENT_DB` | Payment Mock | `payment_mock/payments.db` | Ruta de la base |
| `PAYMENT_PORT` | Payment Mock | `5001` | Puerto |
| `MOCK_TIMEOUT_DELAY` | Payment Mock | `2.0` | Cuánto duerme antes de responder en `timeout` |

> **Importante:** `PAYMENT_TIMEOUT_SECONDS` tiene que ser **menor** que
> `MOCK_TIMEOUT_DELAY`. Si no, el timeout simulado no se simula: el Order API
> recibe la respuesta y el caso 3 nunca se reproduce.

---

## 9. Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

La suite levanta el Payment Mock como servidor HTTP real en un hilo aparte, así que
los timeouts son timeouts de verdad y no excepciones inyectadas. Cada caso del reto
está cubierto como test ejecutable y las aserciones se hacen sobre las filas
persistidas, no solo sobre la respuesta.

---

## 10. Decisiones de diseño

| Decisión | Por qué |
| --- | --- |
| `UNIQUE` + `UPDATE` condicional en vez de locks en memoria | La garantía sobrevive a reinicios y funciona con varios procesos |
| El mock persiste el cobro **antes** de responder | Un timeout debe significar "no sé", no "no se cobró" |
| Reconciliación consulta antes de reintentar | Reintentar a ciegas es cómo se cobra dos veces |
| Reintento permitido solo si el mock responde `404` | Con la clave única del mock, reintentar es seguro: sigue habiendo un solo cobro |
| Reserva con estado (`HELD`/`CONFIRMED`/`RELEASED`) | Liberar dos veces el mismo stock se evita con un `UPDATE ... WHERE status='HELD'` |
| Dinero en centavos enteros | Sin errores de coma flotante |
| Un solo producto por pedido | Límite de alcance explícito, no un olvido (ver `doc/fase-01`) |

---

## 11. Estructura del repositorio

```text
order_api/        Order API: app, capa SQLite, cliente del mock, esquema
payment_mock/     Payment Mock: app, capa SQLite, esquema
tests/            Suite pytest (contratos de esquema y los cinco casos)
postman/          Colección, environment y consultas de evidencia
doc/              Documentación por fases (índice en doc/README.md)
run.ps1           Arranca los dos servicios
AGENTS.md         Stack, convenciones y puertas de calidad
```

La documentación por fases está en [`doc/README.md`](doc/README.md): cada fase tiene
su archivo con decisiones, contratos y verificación observada.
