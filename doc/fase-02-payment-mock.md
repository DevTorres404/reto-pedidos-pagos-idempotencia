# Fase 02 — Payment Mock API

- **Estado:** cerrada
- **Fecha de cierre:** 2026-10-28
- **Documentos previos relevantes:** `fase-01-diseno-estados-y-esquemas.md`

## Objetivo

Tener el procesador de pagos falso funcionando como proceso real: cobra, rechaza y
simula la pérdida de la respuesta **después** de persistir el cobro, y permite
consultar por clave lo que realmente quedó registrado.

## Alcance

**Incluye**

- `POST /payments`, `GET /payments/<key>`, `GET /health`.
- Persistencia en `payments.db` antes de escribir la respuesta.
- Idempotencia por `UNIQUE (idempotency_key)` con detección de reuso.
- Escenario `mode=timeout` con demora real configurable.
- 18 tests de comportamiento y una prueba de humo contra el proceso real.

**No incluye**

- Order API, reservas, inventario, reconciliación (fases 03 a 06).
- Autenticación, rate limiting, HTTPS. Es un mock local.

## Decisiones

| # | Decisión | Alternativa descartada | Motivo |
| --- | --- | --- | --- |
| D1 | Conexiones en autocommit (`isolation_level=None`) | Transacción explícita | El `INSERT` tiene que estar en disco antes de que el proceso se cuelgue a propósito; dejar algo en una transacción abierta invalidaría toda la demostración |
| D2 | El timeout se simula durmiendo **después** del commit | Cortar la conexión antes de responder | El resultado es idéntico desde el cliente (read timeout) pero el orden es verificable en el código, que es lo que se defiende |
| D3 | El mock devuelve el `201` recién al terminar la espera | Responder primero y dormir después | Si responde primero, el cliente nunca ve un timeout y el caso 3 no existe |
| D4 | `charged` se calcula desde `status` (`!= REJECTED`) | Que el cliente lo deduzca | Hace explícito que `CHARGED_TIMEOUT` es dinero movido, no un error |
| D5 | Reuso de clave con otro monto/moneda/pedido → `409 idempotency_key_reuse` | Devolver el pago anterior igual | Un replay legítimo debe ser byte a byte el mismo intento; si cambió el contenido, es un bug del cliente y hay que verlo |
| D6 | La espera del modo `timeout` se configura por `MOCK_TIMEOUT_DELAY` | Valor fijo en el código | La suite corre con `0.0` y la demo usa `2.0`, sin tocar código |
| D7 | `order_id` es texto plano, sin FK | FK lógica o tabla espejo | El mock no puede depender del esquema del Order API; son dos bases con dueños distintos |
| D8 | Validación estricta de tipos (`bool` no cuenta como `int`) | Confiar en la coerción de Flask | `True` es `int` en Python y `amount_cents=true` habría entrado como 1 centavo |

## Contratos implementados

| Método | Ruta | Códigos | Notas |
| --- | --- | --- | --- |
| `POST` | `/payments` | `201`, `200`, `400`, `409` | `200` sólo en replay, con `X-Idempotent-Replay: true` |
| `GET` | `/payments/<key>` | `200`, `404` | Lectura segura y repetible; es el camino de reconciliación |
| `GET` | `/health` | `200` | — |

Respuesta:

```json
{
  "payment_id": "…",
  "idempotency_key": "pay_…",
  "order_id": "…",
  "amount_cents": 9900,
  "currency": "USD",
  "mode": "timeout",
  "status": "CHARGED_TIMEOUT",
  "charged": true,
  "failure_reason": null,
  "created_at": "2026-10-28T00:20:11.123Z",
  "updated_at": "2026-10-28T00:20:11.123Z"
}
```

## Implementación

| Archivo | Responsabilidad |
| --- | --- |
| `payment_mock/app.py` | Factory `create_app`, rutas, validación, orden persistir → dormir → responder |
| `payment_mock/db.py` | Conexión autocommit con `busy_timeout` y WAL; aplicación idempotente del esquema |
| `payment_mock/schema.sql` | DDL de `payments` (fase 01) |
| `tests/test_payment_mock.py` | 18 tests de comportamiento |
| `pyproject.toml` | `pythonpath = ["."]` para que `pytest` resuelva el paquete sin instalar |

## Verificación

```text
$ .\.venv\Scripts\python.exe -m pytest -q
..........................                                               [100%]
26 passed in 0.71s
```

Prueba de humo contra el proceso real (`python -m payment_mock.app`, puerto 5011,
`MOCK_TIMEOUT_DELAY=2.0`, cliente con `timeout=0.5`):

```text
approve            -> 201 APPROVED
replay same key    -> 200 true APPROVED
reject             -> 201 REJECTED
timeout client     -> ReadTimeout
lookup after loss  -> 200 CHARGED_TIMEOUT charged: True
lookup unknown key -> 404 payment_not_found

payments.db rows:
   ('smoke-approve', 'approve', 'APPROVED', 9900)
   ('smoke-reject', 'reject', 'REJECTED', 500)
   ('smoke-timeout', 'timeout', 'CHARGED_TIMEOUT', 100)
total rows: 3
db file exists after shutdown: True
```

Los tres puntos que esta salida demuestra: el cliente efectivamente sufrió un
timeout, el cobro existía antes de que el cliente preguntara, y el replay no agregó
una cuarta fila.

## Evidencia

- `tests/test_payment_mock.py::test_slow_mode_persists_the_charge_before_the_response_is_sent`
  consulta la base desde otro hilo mientras la request sigue esperando.
- `tests/test_payment_mock.py::test_payment_survives_a_service_restart` levanta una
  segunda instancia de la app sobre la misma base.
- Salida de la prueba de humo (arriba), reproducible con el script usado en esta fase.

## Deuda técnica y riesgos

- El timeout depende de una espera real. Es determinista, pero si el cliente tiene un
  timeout mayor que `MOCK_TIMEOUT_DELAY` no se produce la pérdida.
- WAL deja archivos `-wal` y `-shm` junto a la base; están en `.gitignore`.
- No hay límite de conexiones ni protección contra abuso; irrelevante para el reto,
  pero conviene decirlo en el README para que no parezca un olvido.
- El mock no valida que `order_id` exista. No puede: no conoce la otra base.

## Pendientes

- [ ] Commit de la fase.
- [ ] `run.ps1` para levantar ambos servicios (fase 03, cuando exista el Order API).
- [ ] Decidir si el `README.md` documenta `MOCK_TIMEOUT_DELAY` como variable de demo.

## Próximo paso

Fase 03: Order API con flujo feliz — crear pedido, reservar stock con `UPDATE`
condicional, crear la reserva `HELD`, cobrar contra el mock y confirmar. La
integración usa `payment_key = pay_<order_id>`, que es la clave que la fase 05 va a
consultar para reconciliar.
