# Fase 08 — Entrega: README, PDF y video

- **Estado:** cerrada (contenido listo; la grabación y la exportación del PDF son
  tareas humanas)
- **Fecha de cierre:** 2026-10-28
- **Documentos previos relevantes:** todos los anteriores

## Objetivo

Dejar el repositorio presentable y el material de entrega escrito: README en español,
contenido del PDF (arquitectura, tabla de los cinco casos con resultado observado,
evidencia SQL, conclusión) y guion del video de 3 a 5 minutos.

## Alcance

**Incluye**

- `README.md` en español en la raíz: instalación, ejecución, API, los cinco casos,
  evidencia, variables de entorno, decisiones y estructura.
- Este documento con el contenido del PDF y el guion del video.
- Tabla de los cinco casos con resultado **observado**, no esperado.

**No incluye**

- El archivo `.pdf` y el video como binarios. El contenido está listo; exportarlo y
  grabar el video es trabajo humano (y el video tiene que mostrar la voz y la
  pantalla de quien entrega).

## Decisiones

| # | Decisión | Alternativa descartada | Motivo |
| --- | --- | --- | --- |
| D1 | README en español, código y tests en inglés | Todo en inglés | El evaluador lee español; el código sigue la convención del proyecto (`AGENTS.md`) |
| D2 | La tabla de casos incluye el resultado observado de una corrida real | Solo el resultado esperado | "Esperado" no es evidencia. Lo observado sale de la corrida de `newman` y de las consultas SQL |
| D3 | El video se apoya en la colección de Postman, no en `curl` | Comandos sueltos en consola | La colección es el artefacto reproducible que el evaluador va a correr |
| D4 | La conclusión nombra los límites (una línea por pedido, sin scheduler) | Presentar el sistema como producción | Reconocer el alcance acotado es parte de entender el problema |

## Contenido del PDF

Estructura sugerida, una sección por página o dos secciones por página.

### 1. Portada

- Título: *Pedidos, pagos simulados e idempotencia*.
- Nombre, fecha, stack: Python 3.13 + Flask + SQLite.
- Repositorio y comando para correrlo: `.\run.ps1`.

### 2. Problema y enfoque

Tres preguntas que el sistema responde:

1. ¿Qué pasa si el cliente reintenta la misma compra? → la misma clave no crea nada nuevo.
2. ¿Qué pasa si el cobro ocurrió pero la respuesta se perdió? → estado incierto y
   reconciliación contra el cobro real.
3. ¿Qué pasa si el cobro se rechaza? → la reserva se libera exactamente una vez.

Enfoque: dos servicios con bases separadas, garantías en SQLite (`UNIQUE`,
`UPDATE` condicional, `CHECK`), mock que persiste el cobro **antes** de responder.

### 3. Arquitectura

Diagrama de la sección 1 del `README.md`, con la aclaración de que el Order API nunca
abre `payments.db` y el mock nunca abre `orders.db`.

### 4. Máquina de estados

```text
PENDING_PAYMENT ──► PAID
        │           ▲
        ├──────────►└── PAYMENT_UNCERTAIN ──► PAID
        │                                 └─► REJECTED
        ├──► REJECTED     (cobro rechazado → reserva liberada)
        └──► CANCELLED    (stock insuficiente, nunca se cobró)
```

### 5. Los cinco casos

| # | Caso | Acción | Esperado | **Observado** |
| --- | --- | --- | --- | --- |
| 1 | Flujo exitoso | `POST /orders` (`approve`) | `201`, `PAID`, reserva `CONFIRMED` | `201 PAID CONFIRMED`; 1 pedido, 1 reserva, 1 cobro; stock 4/0 |
| 2 | Misma solicitud repetida | Mismo `Idempotency-Key` | `200`, sin efectos nuevos | `200`, `X-Idempotent-Replay: true`, mismo `order_id`; stock y conteos sin cambios |
| 3 | Respuesta perdida | `payment_mode: timeout` → `reconcile` | `202` incierto y luego `PAID` sin recobro | `202 PAYMENT_UNCERTAIN`; el mock tiene `CHARGED_TIMEOUT`; reconcile `200 PAID`; 1 solo cobro |
| 4 | Pago rechazado | `payment_mode: reject` | `REJECTED`, reserva liberada una vez | `201 REJECTED RELEASED`; stock 5/0; reconciliar de nuevo no libera otra vez |
| 5 | Otra intención | Segunda clave con la última unidad | Primera `PAID`, segunda `409` | `201 PAID` + `409 insufficient_stock`; stock 0/0, nunca negativo |

Fuente: corrida de `newman` (32 requests, 56 aserciones, 0 fallos) y consultas de
`postman/evidence.sql`.

### 6. Evidencia de no-duplicados

```text
orders      : 5
reservations: 4
payments    : 4

case1      PAID       CONFIRMED   APPROVED
case3      PAID       CONFIRMED   CHARGED_TIMEOUT
case4      REJECTED   RELEASED    REJECTED
case5-a    PAID       CONFIRMED   APPROVED
case5-b    CANCELLED  (sin reserva)
```

Y la bitácora del pedido recuperado:

```text
ORDER_CREATED → STOCK_RESERVED → PAYMENT_UNKNOWN → PAYMENT_APPROVED → RESERVATION_CONFIRMED
```

### 7. Decisiones clave

Tabla de la sección 10 del `README.md`, resumida a cuatro filas: `UNIQUE` en vez de
memoria, mock que persiste antes de responder, reconciliación que consulta antes de
reintentar, compensación con `WHERE status='HELD'`.

### 8. Conclusión

El punto del ejercicio no es "crear pedidos", es **no repetir efectos**. Todo lo que
garantiza eso está en la base de datos: restricciones, `UPDATE` condicionales y
estados explícitos. La consecuencia práctica es que reiniciar los servicios, correr
la colección otra vez o reconciliar dos veces no cambia el resultado.

Límites reconocidos: un solo producto por pedido, reconciliación manual (sin
scheduler), y claves de idempotencia sin ventana de expiración.

## Guion del video (3–5 minutos)

| Tiempo | Qué se muestra | Qué se dice |
| --- | --- | --- |
| 0:00–0:30 | Repositorio abierto, `README.md` | "Dos servicios Flask con SQLite: Order API en 5000 y Payment Mock en 5001. Cada uno con su base. El reto: que repetir, perder la respuesta o rechazar un cobro no duplique nada." |
| 0:30–1:00 | `.\run.ps1` y los dos `/health` | "Arranco los dos procesos y verifico que respondan. Las bases se crean solas." |
| 1:00–1:45 | Postman, carpeta 1 y 2 | "Caso 1: pedido aprobado, queda PAID con la reserva confirmada. Caso 2: mando la misma clave otra vez y devuelve 200 con el header de replay; miro el inventario y no se movió." |
| 1:45–2:45 | Carpeta 3 | "Caso 3 es el corazón: pido el cobro en modo timeout. El mock guarda el cobro y después no responde a tiempo, así que el pedido queda incierto, 202. Antes de reconciliar consulto el cobro en el mock: existe, CHARGED_TIMEOUT. Reconciliamos: PAID, sin segundo cobro. Reconciliar de nuevo no cambia nada." |
| 2:45–3:30 | Carpeta 4 | "Caso 4: cobro rechazado. El pedido queda REJECTED y el stock vuelve a estar disponible. Reconciliar otra vez no libera dos veces." |
| 3:30–4:00 | Carpeta 5 | "Caso 5: con una sola unidad, dos claves distintas son dos intenciones distintas. La primera pasa, la segunda recibe 409 y el stock queda en cero, nunca negativo." |
| 4:00–4:40 | `postman\run_evidence.py` | "Acá está la prueba: cinco pedidos, cuatro reservas, cuatro cobros. Cero claves repetidas, cero pedidos con dos reservas, cero cobros duplicados. Y la bitácora del pedido recuperado." |
| 4:40–5:00 | `README.md`, sección de decisiones | "La garantía vive en la base: UNIQUE, UPDATE condicionales y estados explícitos. Por eso reiniciar o repetir no rompe nada. Límites: un producto por pedido y reconciliación manual." |

Consejos de grabación:

- Correr `.\run.ps1 -Reset` antes de grabar: el repositorio se entrega **sin bases**,
  así que el primer arranque crea todo desde cero.
- Tener abierta la colección ya importada con el environment seleccionado.
- Grabar la carpeta 3 sin cortes: el timeout de 1 segundo es parte de la demostración.
- Al mostrar el SQL, usar `.\.venv\Scripts\python.exe postman\run_evidence.py`.

## Verificación

```text
$ .\.venv\Scripts\python.exe -m pytest -q
..............................................                           [100%]

$ node newman.js run postman/reto-pedidos-pagos.postman_collection.json -e ...
  requests 32, assertions 56, failed 0
```

El `README.md` fue verificado contra el código: cada endpoint listado existe
(`@app.get`/`@app.post` en `order_api/app.py` y `payment_mock/app.py`), cada variable
de entorno se lee con `os.environ.get`, y los comandos fueron ejecutados en esta
máquina.

## Deuda técnica y riesgos

- El PDF y el video son artefactos humanos: el contenido está listo, la grabación no.
- El README muestra resultados de una corrida concreta. Si se cambia el escenario, los
  números cambian; las consultas se vuelven a correr.

## Pendientes

- [x] README en español con instalación, ejecución, API y los cinco casos.
- [x] Contenido del PDF en este documento.
- [x] Guion del video de 3 a 5 minutos.
- [ ] Exportar el PDF a partir de este documento.
- [ ] Grabar el video.
- [ ] Commit y publicación del repositorio (decisión del autor).

## Próximo paso

Ninguno técnico. El sistema está completo y verificado; queda la producción de los
artefactos de entrega.
