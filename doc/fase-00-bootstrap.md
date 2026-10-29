# Fase 00 — Bootstrap del repositorio y contrato de dominio

- **Estado:** cerrada
- **Fecha de cierre:** 2026-10-28
- **Documentos previos relevantes:** ninguno (fase inicial)

## Objetivo

Dejar un repositorio ejecutable y con reglas explícitas, para que las fases
siguientes escriban código sin renegociar stack, estructura ni criterios de
aceptación.

## Alcance

**Incluye**

- Repositorio Git con rama `main`.
- Entorno virtual con dependencias fijadas.
- Índice CodeGraph del proyecto.
- `AGENTS.md` con stack, layout, convenciones y gates de calidad.
- Skill de proyecto `reto-pedidos-pagos` con las invariantes del enunciado.
- Registro de skills y esta carpeta de documentación por fases.

**No incluye**

- Código de los servicios, esquema SQL, tests ni colección Postman.
- Commits: el repositorio queda con todo sin trackear a propósito.

## Decisiones

| # | Decisión | Alternativa descartada | Motivo |
| --- | --- | --- | --- |
| D1 | Dos procesos Flask independientes, puertos 5000 y 5001, una base SQLite cada uno | Un solo servicio con dos blueprints | El enunciado evalúa la recuperación *entre* servicios: el timeout tiene que ser una falla de red real, no una llamada en proceso |
| D2 | Idempotencia por restricción `UNIQUE` en la base | Diccionario en memoria o lock de proceso | Se evalúa después de reiniciar los servicios; el estado en memoria muere con el proceso |
| D3 | El mock persiste el cobro **antes** de cortar la respuesta | Cortar antes de persistir | Un timeout significa "el cliente no supo el resultado", nunca "no hubo cobro". Invertirlo haría imposible la reconciliación |
| D4 | Reconciliación por `GET /payments/<idempotency_key>`, nunca por reintento de `POST` | Reintentar el `POST` con la misma clave | El enunciado lo prohíbe explícitamente: no recobrar a ciegas |
| D5 | Dinero en enteros (centavos) | `float` o `Decimal` serializado | Evita errores de redondeo en comparaciones de evidencia |
| D6 | Estado de pedido persistido antes de responder | Escribir la respuesta y persistir después | Un reinicio en el medio dejaría estado imposible de reconciliar |
| D7 | Código, tests y commits en inglés; `README.md` y PDF en español | Todo en español | El código sigue convención técnica estándar; el evaluador lee español |

## Contratos resultantes

Máquina de estados del pedido, fijada como default de la Fase 01:

| Estado | Significado | Terminal |
| --- | --- | --- |
| `PENDING_PAYMENT` | Pedido creado, stock reservado, cobro no resuelto | no |
| `PAYMENT_UNCERTAIN` | Cobro enviado, respuesta perdida; requiere reconciliación | no |
| `PAID` | Cobro aprobado, reserva confirmada | sí |
| `REJECTED` | Cobro rechazado, reserva liberada | sí |
| `CANCELLED` | Pedido abandonado antes de cobrar (por ejemplo, sin stock) | sí |

Transiciones permitidas: `PENDING_PAYMENT → PAID | REJECTED | PAYMENT_UNCERTAIN`,
`PAYMENT_UNCERTAIN → PAID | REJECTED`. Cualquier otra transición es un bug.

Estados del mock de pagos: `APPROVED`, `REJECTED`, `CHARGED_TIMEOUT` (cobro
persistido, respuesta descartada). Entrada `mode` para forzar el escenario.

Superficie HTTP mínima: `POST /orders`, `GET /orders/<id>`,
`POST /orders/<id>/reconcile`, `POST /payments`,
`GET /payments/<idempotency_key>`.

## Implementación

| Archivo | Responsabilidad |
| --- | --- |
| `AGENTS.md` | Contexto del proyecto para agentes: stack, layout, convenciones, gates |
| `.agents/skills/reto-pedidos-pagos/SKILL.md` | Invariantes de dominio y anti-patrones que pierden puntos |
| `.atl/skill-registry.md` | Índice de skills con rutas exactas para delegación |
| `.gitignore` | Excluye `*.db`, `.venv/`, `.atl/`, `.codegraph/` |
| `requirements.txt` | Dependencias fijadas (`Flask`, `pytest`, `requests`) |
| `doc/` | Esta documentación por fases |

## Verificación

```text
$ git --version
git version 2.54.0.windows.1

$ python --version
Python 3.13.13

$ .\.venv\Scripts\python.exe -m pip list --format=freeze
Flask==3.1.3
pytest==9.2.0
requests==2.34.2

$ gentle-ai codegraph init --cwd .
CodeGraph initialized: C:\Users\Damian\Documents\workspace\retotecnico

$ codegraph status
Files: 0   Nodes: 0   Edges: 0   [OK] Index is up to date
```

El índice tiene 0 archivos porque todavía no existe código fuente. No es un fallo.

## Evidencia

- `doc/README.md` con el índice de fases.
- `.atl/skill-registry.md` con 11 skills indexadas.
- La skill `reto-pedidos-pagos` quedó visible para el runtime sin reiniciar sesión.

## Deuda técnica y riesgos

- Sin commits: conviene hacer el commit inicial antes de la Fase 01 para tener
  punto de retorno.
- CodeGraph sin nodos hasta que exista código Python.

## Pendientes

- [ ] Commit inicial del bootstrap.
- [ ] Decidir si el inventario inicial se siembra por script o por endpoint de admin
      (se resuelve en Fase 01).

## Próximo paso

Fase 01: fijar esquema SQL de ambas bases, contratos HTTP con sus códigos de estado y
la tabla de eventos/evidencia que se va a consultar en las pruebas de Postman.
