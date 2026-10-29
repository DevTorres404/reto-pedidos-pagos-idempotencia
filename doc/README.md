# Documentación por fases — retotecnico

Repositorio del reto técnico: **pedidos, pagos simulados e idempotencia**
(Flask + SQLite, dos servicios, evidencia en Postman).

El enunciado completo está en `../reto_tecnico_pedidos_pagos_idempotencia.md` y es la
fuente de verdad. Si un documento de fase lo contradice, gana el enunciado.

## Convención

Cada fase del desarrollo tiene **un documento propio** en esta carpeta. El documento
se escribe *al cerrar* la fase y contiene lo que la siguiente fase necesita saber sin
volver a leer el código:

- qué se buscaba y qué se logró,
- decisiones tomadas y su justificación,
- contratos que la fase expone (endpoints, esquemas, estados),
- evidencia de verificación (comandos y resultados observados),
- deuda técnica, riesgos y pendientes,
- próximo paso concreto.

Numeración correlativa de dos dígitos: `fase-NN-<slug>.md`. No se renombran fases ya
cerradas; si una decisión cambia, se agrega una sección `## Enmienda` con fecha.

Plantilla: `_plantilla-fase.md`.

## Índice de fases

| Fase | Documento | Objetivo | Estado |
| --- | --- | --- | --- |
| 00 | `fase-00-bootstrap.md` | Repositorio, tooling, convenciones y contrato de dominio | Cerrada |
| 01 | `fase-01-diseno-estados-y-esquemas.md` | Máquina de estados, esquema SQL y contratos HTTP de ambos servicios | Cerrada |
| 02 | `fase-02-payment-mock.md` | Payment Mock con cobro persistido antes de la respuesta, y consulta por clave | Cerrada |
| 03 | `fase-03-order-api-flujo-feliz.md` | Crear pedido, reservar stock, cobrar aprobado, consultar pedido | Cerrada |
| 04 | `fase-04-idempotencia.md` | Clave única por operación en ambos servicios; replay sin efectos nuevos | Cerrada |
| 05 | `fase-05-recuperacion-timeout.md` | Estado incierto, reconciliación contra el mock, sin recobro a ciegas | Cerrada |
| 06 | `fase-06-compensacion-rechazo.md` | Rechazo libera reserva exactamente una vez; stock nunca negativo | Cerrada |
| 07 | `fase-07-postman-y-evidencia.md` | Colección reproducible con los 5 casos y consultas SQL de evidencia | Cerrada |
| 08 | `fase-08-entrega.md` | README en español, PDF y guion del video | Cerrada |

Las fases 02 y 03 pueden invertirse; 04, 05 y 06 dependen de ambas.

## Estado del sistema

Todas las fases están cerradas. Evidencia de la última corrida completa:

- `pytest`: 46 tests en verde (20 Order API, 18 Payment Mock, 8 contrato de esquema).
- `newman`: 32 requests, 56 aserciones, 0 fallos, 4.2 s.
- SQLite: 5 pedidos, 4 reservas, 4 cobros; cero claves repetidas.

Lo único que queda es humano: exportar el PDF, grabar el video y decidir el commit.

## Regla de trabajo

Ninguna fase se marca cerrada sin: suite `pytest` en verde, evidencia observada
(no inferida) y su documento escrito en esta carpeta. Un documento de fase nunca
reemplaza al código ni a la colección de Postman: es el mapa, no el territorio.
