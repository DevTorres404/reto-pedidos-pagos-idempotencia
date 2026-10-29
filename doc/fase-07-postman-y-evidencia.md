# Fase 07 — Colección de Postman y evidencia SQL

- **Estado:** cerrada
- **Fecha de cierre:** 2026-10-28
- **Documentos previos relevantes:** `fase-03` a `fase-06`

## Objetivo

Tener una colección que un tercero pueda importar, ejecutar de punta a punta y ver
los cinco casos del reto pasar sin tocar código, más consultas SQL que prueben que
no hay duplicados.

## Alcance

**Incluye**

- `postman/reto-pedidos-pagos.postman_collection.json` con una carpeta por caso.
- `postman/local.postman_environment.json` con las URLs y el SKU de demo.
- `postman/evidence.sql` con las consultas de evidencia.
- Verificación real con `newman`: 32 requests, 56 aserciones, 0 fallos.

**No incluye**

- Tests de carga o concurrencia agresiva. La concurrencia está cubierta por el
  diseño (`BEGIN IMMEDIATE` + `UPDATE` condicional) y por los tests de esquema, no
  por un benchmark.

## Decisiones

| # | Decisión | Alternativa descartada | Motivo |
| --- | --- | --- | --- |
| D1 | Cada carpeta reinicia ambos servicios (`/admin/reset`) y siembra el inventario como **requests visibles** | Un `pre-request script` con `pm.sendRequest` | Los `sendRequest` son asíncronos y la request principal podría salir antes de que termine el reset. Además, en el video se ve exactamente qué estado se prepara |
| D2 | Claves de idempotencia fijas por caso (`case1-key`, …) | Claves con `{{$timestamp}}` | Como cada carpeta resetea, las claves fijas no colisionan y hacen la colección legible y repetible |
| D3 | El caso 3 consulta el cobro en el mock (`3.4`) **antes** de reconciliar | Ir directo a `reconcile` | Es la evidencia central del reto: el cobro existe aunque la respuesta se perdió. Si no se muestra, la recuperación parece magia |
| D4 | El caso 4 reconcilia después del rechazo y vuelve a leer el stock | Confiar en la primera respuesta | Prueba que la compensación es idempotente, no solo que ocurrió |
| D5 | El `evidence.sql` usa `ATTACH` para unir pedidos y cobros | Copiar tablas a mano | Permite una sola consulta de correlación. El `ATTACH` es **solo** del script de evidencia; los servicios nunca abren la base del otro |
| D6 | Un archivo `evidence.sql` versionado | Pegar las queries en el PDF | Se puede volver a correr y el resultado se reproduce |

## Contratos resultantes

Estructura de la colección:

```text
1. Flujo exitoso
   1.0 Reset Order API
   1.1 Reset Payment Mock
   1.2 Sembrar inventario (5 unidades)
   1.3 Crear pedido aprobado          → 201 PAID, guarda order_id
   1.4 Consultar pedido               → 200 PAID

2. Repeticion de la misma solicitud
   2.0–2.2 reset + seed
   2.3 Primer envio (crea)            → 201
   2.4 Reenvio con la misma clave     → 200 + X-Idempotent-Replay
   2.5 Verificar que el stock no se movio dos veces → 4 disponibles, 0 reservadas

3. Respuesta perdida despues del cobro
   3.0–3.2 reset + seed
   3.3 Crear pedido con timeout       → 202 PAYMENT_UNCERTAIN
   3.4 Consultar el pago en el mock   → 200 CHARGED_TIMEOUT, charged=true
   3.5 Reconciliar el pedido          → 200 PAID
   3.6 Reconciliar de nuevo           → 200 PAID (no recobra)
   3.7 El pago sigue siendo uno solo  → misma clave

4. Pago rechazado
   4.0–4.2 reset + seed
   4.3 Crear pedido con pago rechazado → 201 REJECTED, reserva RELEASED
   4.4 El stock volvio a estar disponible → 5 / 0
   4.5 Reconciliar no libera dos veces
   4.6 El stock sigue igual           → 5 / 0

5. Otra intencion de compra
   5.0–5.2 reset + seed 1 unidad
   5.3 Pedido A                        → 201 PAID
   5.4 Pedido B (otra clave)           → 409 insufficient_stock
   5.5 El stock llego a cero           → 0 / 0
```

## Implementación

| Archivo | Responsabilidad |
| --- | --- |
| `postman/reto-pedidos-pagos.postman_collection.json` | Los cinco casos con aserciones y variables de colección |
| `postman/local.postman_environment.json` | `order_api`, `payment_api`, `sku` |
| `postman/evidence.sql` | Ocho consultas de evidencia, incluida la correlación pedido ↔ cobro |
| `postman/run_evidence.py` | Ejecutor de esas consultas sin depender de que el CLI `sqlite3` esté instalado (útil en Windows y en CI) |

## Verificación

Colección ejecutada con `newman` contra los dos servicios como procesos reales,
bases recién borradas:

```text
$ node newman.js run postman/reto-pedidos-pagos.postman_collection.json \
                 -e postman/local.postman_environment.json
┌─────────────────────────┬────────────────────┬───────────────────┐
│                         │           executed │            failed │
├─────────────────────────┼────────────────────┼───────────────────┤
│              iterations │                  1 │                 0 │
│                requests │                 32 │                 0 │
│            test-scripts │                 32 │                 0 │
│              assertions │                 56 │                 0 │
├─────────────────────────┴────────────────────┴───────────────────┤
│ total run duration: 4.2s                                         │
│ average response time: 51ms [min: 7ms, max: 1049ms]              │
└──────────────────────────────────────────────────────────────────┘
```

El máximo de 1049 ms es el caso 3: el timeout de cliente configurado en 1 s.

```text
$ .\.venv\Scripts\python.exe -m pytest -q
..............................................                           [100%]
```

## Evidencia

Consultas listas para el PDF, en `postman/evidence.sql`:

| # | Consulta | Qué prueba |
| --- | --- | --- |
| 1 | Conteo por tabla | 5 pedidos, 4 reservas, 4 cobros |
| 2 | Claves repetidas en `orders` | 0 filas |
| 3 | Pedidos con más de una reserva | 0 filas |
| 4 | Claves repetidas en `payments` | 0 filas |
| 5 | Estado final por clave | Tabla de los cinco casos |
| 6 | Cobros `CHARGED_TIMEOUT` + su pedido | La respuesta perdida y la recuperación |
| 7 | Bitácora de transiciones | El camino, no solo el resultado |
| 8 | Stock | Nunca negativo |

## Deuda técnica y riesgos

- La colección depende de que los puertos 5000 y 5001 estén libres. Si no, hay que
  cambiar el environment (o las variables `ORDER_PORT` / `PAYMENT_PORT`).
- `newman` **no** es una dependencia del repositorio: es una herramienta global
  (`npm install -g newman`, v6.2.3 en la máquina de verificación). La entrega usa
  Postman; para CI convendría agregarlo a un `package.json` con `npx newman`.
- `sqlite3` CLI (v3.53.4) también es una herramienta global, no un requisito del
  proyecto: `postman/run_evidence.py` cubre el mismo caso sin instalarlo.
- Las aserciones validan forma y estado, no tiempos. El caso 3 valida el resultado
  del timeout, no cuánto tardó.

## Verificación de herramientas

Ambos caminos de evidencia se ejecutaron en esta máquina y dan el mismo resultado:

```text
$ sqlite3 --version
3.53.4 2026-07-24 ...

$ sqlite3 -header -column order_api/orders.db ".read postman/evidence.sql"
table_name    rows
orders        5
reservations  4
payments      4

$ newman --version
6.2.3

$ newman run postman\reto-pedidos-pagos.postman_collection.json -e postman\local.postman_environment.json
requests 32, assertions 56, failed 0, 4.3s
```

## Pendientes

- [x] Colección con los cinco casos, reproducible desde cero.
- [x] `evidence.sql` con las consultas de no-duplicados.
- [x] Verificación real con newman.

## Próximo paso

Fase 08: README en español, contenido del PDF y guion del video.
