# Selective ACK (SACK)

Protocolo de transporte confiable sobre UDP para transferir archivos. Comparte
el header de 12 bytes con **Stop & Wait** y se diferencia por el nibble `protocol = 2`.

---

## 1. Qué es SACK, en simple

UDP manda datagramas y no garantiza nada: se pueden perder, llegar duplicados o
llegar fuera de orden. Nosotros los detectamos y los resolvemos con un mecanismo
de retransmisión.

El problema aparece cuando en medio de una transferencia se pierde **un** paquete:

```text
emisor  --->  [1] [2] [3] [4] [5]      (se pierde el [3])
emisor  <---  ACK=1  ACK=2   --   ACK=4  ACK=5
```

El receptor ya tiene los paquetes 1, 2, 4 y 5, pero le falta el 3.

> En este ejemplo numeramos por paquete para que se entienda. En el protocolo
> real la numeración es por bytes, así que los valores del `ack` serían
> números de byte y no de paquete.

**Un protocolo simple** solo puede responder `ACK=2`, diciendo "tengo todo hasta el
2". No le dice al emisor que los paquetes 4 y 5 ya están, así que el emisor no
tiene forma de aprovecharlos: no le queda más que esperar y volver a pedir el 3.
Y si se pierde otro más adelante, repite todo desde el 2.

**SACK agrega una línea al ACK.** En vez de solo `ACK=2`, manda:

```text
ACK=2   SACK=[(4,6)]
```

que se lee: *"tengo todo hasta el 2, y además ya tengo del 4 al 6"*. (El 4 y el
5 son contiguos, así que los fusionamos en un solo bloque.)

Con esa información el emisor sabe que solo falta el 3, y que **no tiene que
volver a mandar ni el 4 ni el 5**. Reenvía únicamente el 3, y el receptor, que
ya los tenía guardados, salta directo de `ACK=2` a `ACK=6`.

---

## 2. Qué le agrega a Stop & Wait

| | Stop & Wait | SACK |
| --- | --- | --- |
| Datos en vuelo | 1 a la vez | 4 a la vez (`cwnd`) |
| Qué llega fuera de orden | se descarta | se guarda en un buffer |
| El ACK dice | hasta dónde tengo | hasta dónde tengo **+ qué bloques tengo sueltos** |
| Al perderse un paquete | se retransmite **todo** desde el hueco, de a uno | se retransmite **solo** el hueco |
| Recovery de una pérdida | espera 1 s (timeout) | ~1 RTT, por *fast retransmit* |

---

## 3. Los mecanismos

**ACK acumulativo.** El campo `ack` siempre significa *"tengo todo hasta acá"*.
No dice cuánto, solo el próximo byte que espera.

**Bloques SACK.** Van en el campo `opciones` del header, en formato TLV
(Type-Length-Value), siguiendo la [opción SACK del RFC 2018,
sección 3](https://www.rfc-editor.org/info/rfc2018/#section-3). Cada bloque es un
intervalo `[inicio, fin)` de bytes, con 4 bytes cada extremo. Mandamos **máximo 4
bloques** porque con `cwnd = 4` nunca puede haber más de 3 segmentos fuera de
orden. Si no hay nada fuera de orden, no se manda la opción y el ACK queda en 12
bytes, igual que en Stop & Wait.

**Fast retransmit.** Cuando el emisor recibe **3 ACKs iguales** sabe que hay un
hueco, porque tres segmentos posteriores llegaron y el hueco no se llenó. Retransmite
solo ese hueco, sin esperar el timer. Ese es el salto de 1 s a un RTT.

**Ventana de envío.** El emisor puede tener 4 segmentos en vuelo. `cwnd = 4` es el
mínimo posible para el fast retransmit: hacen falta 3 segmentos **después** del
hueco para generar los 3 ACKs duplicados. Con menos no se puede.

**Timeout.** El respaldo. 1 segundo fijo (en el resto del documento, `1 s`), y
retransmite el hueco. Se sigue necesitando para dos casos: cuando no quedan 3
segmentos después del hueco, y cuando el propio fast retransmit se pierde.

---

## 4. Decisiones, y por qué

| Decisión | Valor | Por qué |
| --- | --- | --- |
| Transporte | UDP | Es lo que pide el enunciado |
| Payload máximo | 1400 B | Igual que SW. Con header y opciones entra en la MTU de 1500 |
| `cwnd` | 4 | El mínimo que habilita el fast retransmit. Con 8 duplica el throughput |
| Bloques SACK | 4 | Con `cwnd = 4` nunca hay más de 3 fuera de orden |
| Timeout | 1.0 s fijo | Igual que SW. Sin backoff, para que el análisis comparativo sea justo |
| `ISN` | 0 | Igual que SW, para que los dos protocolos sean comparables |
| Numeración | Por bytes | Como TCP. Un `seq` es el número del primer byte del segmento |
| `SYN` | Vacío | --- |
| Handshake | El mismo 3-way de SW | Es idéntico byte a byte, no inventamos otro |

---

## 5. Qué hace cada archivo

En `src/lib/protocols/selective_ack/`:

| Archivo | Qué hace |
| --- | --- |
| `selective_ack.py` | Sockets, handshake, y los bucles `send()` y `recv()`. Es el que habla con el kernel |
| `ack_sender.py` | Estado del emisor: la ventana de 4 segmentos, qué está confirmado, qué está bien con los SACK, los timers y cuándo retransmitir |
| `ack_receiver.py` | Estado del receptor: qué byte espera, qué segmentos llegaron fuera de orden y qué bloques SACK tiene que anunciar |
| `sack_option.py` | La opción SACK en sí. Codifica y decodifica los bloques a bytes, y las operaciones sobre intervalos (fusionar, descartar, "¿este bloque cubre este segmento?") |

La idea del reparto: los dos archivos del medio (`ack_sender` y `ack_receiver`)
son **lógica pura**, no tocan sockets: manejan estado y números, nada más.
`sack_option` también, son funciones sobre listas de intervalos. Recién
`selective_ack.py` es el que habla con el sistema operativo.

---

## 6. Notas de implementación (detalles)

### 6.1 Tamaño del campo HLEN

El campo `hlen` (Header Length) ocupa **8 bits** en el header (byte 2). Su valor indica la longitud total del header en bytes, incluyendo las opciones. El valor mínimo es **12 bytes** (sin opciones). Cuando hay bloques SACK presentes, `hlen = 12 + (2 + 8*N)` para N bloques. Con 4 bloques esto es 12 + 34 = 46 bytes. El ancho de 8 bits permite hasta 255 bytes de header, por lo que las opciones utilizadas (máx. 4 bloques) entran cómodamente sin riesgo de truncamiento.

### 6.2 `RECV_BUFFER` (tamaño del buffer de recepción)

En `selective_ack.py` se define `RECV_BUFFER = 2048` bytes. Este valor corresponde al parámetro `bufsize` de `socket.recvfrom()` y determina la cantidad máxima de bytes que el sistema operativo entrega por llamada.

**Justificación del valor (2048):**

- Tamaño máximo estimado de un datagrama SACK: header base (12 bytes) + opción SACK con 4 bloques (2 + 8*4 = 34 bytes) + payload máximo (1400 bytes) = **1446 bytes**.
- Se elige 2048 bytes (un múltiplo de potencia de 2) para dar un **margen de ~600 bytes** sobre ese máximo, evitando trabajar justo en el límite.
- Un buffer menor a 1446 podría provocar que `recvfrom()` trunque el paquete (entregando menos bytes de los recibidos), lo cual corrompería el parsing del header/opciones. Con 2048 aseguramos recibir el datagrama completo en una sola lectura.
- Importante: esto **no es** la ventana de recepción (`RWIND = CWND * MAX_PAYLOAD_SIZE = 5600 bytes`). `RECV_BUFFER` es el buffer de lectura del socket (capa de I/O), mientras que `RWIND` modela la cantidad de datos que el receptor puede almacenar fuera de orden para reensamblarlos (memoria lógica del protocolo).
