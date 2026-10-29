# Reto técnico: pedidos, pagos simulados e idempotencia

Construyan y prueben una API que pueda recuperarse cuando un pago se procesa, pero su respuesta se pierde.

| **👥 EQUIPOS**<br>3 integrantes | **🧩 TECNOLOGÍA**<br>Flask + SQLite | **🧪 PRUEBAS**<br>Postman / Bruno | **📤 ENTREGA**<br>PDF + código + video |
| ------------------------------- | ----------------------------------- | --------------------------------- | -------------------------------------- |

## El problema que van a resolver

Una tienda recibe una solicitud para crear un pedido y reservar inventario. El pedido se registra y el servicio de pago simulado alcanza a registrar el cobro, pero la respuesta del pago se pierde. El backend no sabe si debe reintentar, consultar el estado o cancelar la reserva. Si repite la operación a ciegas, podría generar efectos duplicados. Usarán SQLite para conservar los estados y comprobar qué ocurrió incluso después de reiniciar los servicios.

**Postman** → **Order API · Flask** → **Payment Mock API · Flask**  
Dos servicios locales y datos persistidos para poder consultar el estado después de una falla.

## Qué deben construir

1. **Order API:** crear pedidos, reservar inventario, guardar el estado de la operación y permitir consultar un pedido. Debe persistir los datos en SQLite entre reinicios.
2. **Payment Mock API:** simular un pago aprobado, rechazado y un caso de *timeout después de registrar el cobro*. Debe persistir su registro de pagos en SQLite. Este servicio falso no procesa tarjetas ni usa dinero real.
3. **Idempotencia:** aceptar una clave estable por operación y protegerla con una restricción de unicidad en la base. Si llega de nuevo la misma solicitud con la misma clave, devolver el pedido/resultado conocido sin crear otra reserva ni otro cobro.
4. **Recuperación:** consultar el estado persistido del pago cuando la respuesta quedó incierta. No reintentar un cobro sin comprobar antes qué ocurrió.
5. **Compensación:** si el pago termina rechazado después de reservar inventario, liberar la reserva y dejar el pedido en un estado final coherente.

| Componente           | Responsabilidad mínima                                                                       | Datos que debe conservar                                               |
| :------------------- | :------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------- |
| **Order API**        | Recibir pedido, controlar inventario, iniciar pago y exponer el estado.                      | SQLite propia: clave única de idempotencia, pedido, estado y reserva.  |
| **Payment Mock API** | Simular aprobación, rechazo o cobro con respuesta perdida; consultar el resultado por clave. | SQLite propia: clave de pago única y resultado persistido del intento. |
| **Postman**          | Ejecutar y repetir solicitudes para demostrar el comportamiento.                             | Colección exportada y respuestas HTTP relevantes.                      |

## Pruebas obligatorias en Postman

| Caso                                       | Qué hacen                                                                                     | Qué deben demostrar                                                                  |
| :----------------------------------------- | :-------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------- |
| **1. Flujo exitoso**                       | Crear pedido con pago aprobado.                                                               | Un pedido, una reserva, un cobro y estado final correcto.                            |
| **2. Repetición de la misma solicitud**    | Reenviar el pedido con la misma clave de idempotencia.                                        | Se reconoce la operación previa; no se duplica pedido, reserva ni cobro.             |
| **3. Respuesta perdida después del cobro** | Hacer que el mock registre el cobro y luego simule un timeout; consultar/recuperar el pedido. | El backend resuelve el estado consultando el pago y no vuelve a cobrar a ciegas.     |
| **4. Pago rechazado**                      | Forzar rechazo del pago después de iniciar el pedido.                                         | El pedido queda rechazado/cancelado y la reserva se libera una sola vez.             |
| **5. Otra intención de compra**            | Enviar otro pedido con una clave distinta cuando queda una unidad.                            | La nueva solicitud se trata como operación distinta y el stock nunca queda negativo. |

**Importante:** una clave repetida representa el mismo intento; una clave diferente representa otra intención. No se pide prometer “exactamente una vez” de forma mágica: deben demostrar cómo evitan duplicar los efectos y cómo averiguan el estado después de una falla.

## Cómo organizar el trabajo

1. Definan los estados del pedido y qué significan (por ejemplo: pendiente de pago, pago incierto, pagado, rechazado).
2. Implementen primero el flujo exitoso y luego agreguen claves de idempotencia y recuperación.
3. Exporten una colección de Postman que permita repetir los cinco casos.
4. Registren evidencia del estado del pedido, inventario y pagos antes/después de las pruebas.

## Entrega en Moodle

Suban **un PDF por equipo**. Un integrante puede realizar el envío en nombre del grupo. El PDF debe contener:

- Nombres de los integrantes y enlace al repositorio con el código fuente, README e instrucciones para ejecutar ambos servicios.
- La colección exportada de Postman dentro del repositorio.
- Una tabla con los cinco casos, resultado esperado y resultado obtenido; agreguen capturas legibles de Postman y evidencia consultada en SQLite que muestre que no se duplicaron pedidos, reservas ni cobros.
- Enlace a un video de 3 a 5 minutos donde demuestren, como mínimo, el timeout posterior al cobro y el reintento con la misma clave. Verifiquen que el docente pueda abrir el enlace.
- Una conclusión breve: ¿qué estado persistieron para recuperarse y cómo comprobaron que no hubo efectos duplicados?

**Gestión del equipo:** la organización del trabajo y de las intervenciones en el video corresponde al equipo. La entrega y calificación son grupales; no se solicita una entrega individual adicional. El repositorio debe ser accesible para revisión.

## Rúbrica de evaluación · 100 puntos

| Criterio                                          | Logro destacado                                                                                                              | Logro adecuado                                                                                  | En desarrollo                                                                                      | Insuficiente                                                                       |
| :------------------------------------------------ | :--------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------- |
| **API y persistencia SQLite**<br>20 pts           | 20 · Ambos servicios se ejecutan con instrucciones claras y conservan estados consultables en SQLite después de reiniciarse. | 15 · El flujo principal funciona; hay una omisión menor en estados o documentación.             | 10 · La API funciona parcialmente o depende demasiado de estado solo en memoria.                   | 0–5 · No se logra ejecutar o consultar el flujo básico.                            |
| **Idempotencia de pedidos y pagos**<br>25 pts     | 25 · La misma clave devuelve el resultado conocido y no duplica pedido, reserva ni cobro.                                    | 19 · Evita duplicar los efectos principales, con una omisión menor de manejo de estado.         | 13 · Detecta alguna repetición, pero puede duplicar al menos un efecto o no conserva el resultado. | 0–6 · No diferencia reintento de operación nueva.                                  |
| **Falla simulada y recuperación**<br>25 pts       | 25 · El mock persiste el cobro antes del timeout y el backend reconcilia estado sin cobrar a ciegas.                         | 19 · La recuperación funciona, aunque la transición de estado o evidencia podría ser más clara. | 13 · Simula la falla, pero la recuperación es manual, incompleta o poco segura.                    | 0–6 · No permite observar la incertidumbre ni recuperar el pedido.                 |
| **Inventario y pago rechazado**<br>15 pts         | 15 · El inventario no queda negativo y la reserva se libera correctamente ante rechazo.                                      | 11 · Los resultados son correctos con una inconsistencia menor de estado o evidencia.           | 7 · Trata rechazo o stock parcialmente, sin asegurar el estado final.                              | 0–3 · Permite stock negativo o deja efectos sin resolver.                          |
| **Postman, repositorio y demostración**<br>15 pts | 15 · Colección reproducible, README claro, PDF verificable y video de 3–5 min accesible.                                     | 11 · Los entregables están disponibles, con detalles menores que dificultan reproducirlos.      | 7 · Falta un entregable o la demostración/evidencia no permite verificar varios casos.             | 0–3 · No se puede acceder al código, ejecutar las pruebas o abrir la demostración. |

**Criterio de éxito:** después de repetir solicitudes y simular una respuesta perdida, el equipo puede inspeccionar SQLite y demostrar cuántos pedidos, reservas y cobros existen, que cada clave produjo como máximo un efecto, y cómo recuperó el estado.
