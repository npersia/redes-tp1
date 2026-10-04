# Suite de pruebas de Stop & Wait y Selective ACK

402 tests, sin dependencias externas (solo `unittest` y `trace` de la stdlib).

```bash
python3 tests/run_tests.py              # correr todo
python3 tests/run_tests.py --cobertura  # + reporte de lineas no ejecutadas
python3 -m unittest test_send -v        # un modulo suelto (desde tests/)
```

Cobertura actual: **100% de las lineas** de `stop_wait.py`, `packet.py` y de
`selective_ack/` (`sack_option.py`, `ack_receiver.py`, `ack_sender.py`,
`trace.py`), y 191/192 de `selective_ack.py`: la que falta es el `break` de
`send()` tras el bucle de envio, inalcanzable (al salir de ese bucle siempre
queda al menos un segmento en la ventana).

## Como esta armado

`netsim.py` no reemplaza a UDP: envuelve sockets UDP reales sobre loopback y
se interpone unicamente en `sendto`. Los timeouts, el bloqueo y la semantica
de `recvfrom` son los del sistema operativo, asi que los reintentos que se
prueban son los de verdad. Lo unico que cambia es la escala de tiempo
(`TEST_TIMEOUT = 0.05` en vez de `TIMEOUT = 1.0`).

Dos mecanismos para provocar escenarios:

- **Politicas de salida** (`drop_nth`, `drop_data_nth`, `drop_acks_nth`,
  `dup_nth`, `lossy`, `("delay", s)`): deciden si cada datagrama sale, se
  descarta, se duplica o se demora. Sirven para perdida y reordenamiento.
- **`RawPeer`**: un socket pelado para inyectar datagramas armados a mano
  (numeros de ACK invalidos, paquetes de un tercero, datagramas truncados).
  Se usa para ejercitar cada condicion anidada por separado, sin depender de
  que el otro extremo se porte de una manera dificil de provocar.

`net.dump()` imprime la traza completa de la corrida, util para depurar un
test que falla.

## Modulos

| Archivo | Que cubre |
|---|---|
| `test_packet.py` | Serializado de la cabecera: flags, hlen, seq/ack, opciones, payload. |
| `test_connect.py` | Handshake lado cliente: retransmision del SYN, SYN-ACK invalidos, agotamiento de reintentos. |
| `test_accept.py` | Handshake lado servidor: socket efimero, retransmision del SYN-ACK, SYN duplicados, apagado. |
| `test_send.py` | Chunking, FIN, avance del seq, retransmision, ACK viejos/ajenos, ERR, agotamiento. |
| `test_recv.py` | Reensamblado, duplicados, huecos, ACK acumulativo, ERR, cierre. |
| `test_e2e.py` | Cliente y servidor reales con perdida determinista y aleatoria reproducible. |
| `test_integracion.py` | `client.upload/download`, `server.handle_connection`, `Dispatcher`, factory. |
| `test_sack_option.py` | Opcion SACK (TLV): armado, parseo, malformadas; `add_block`, `discard_below`, `covers`. |
| `test_ack_receiver.py` | Receptor SACK: entrega en orden, buffer, duplicados, solapamiento, ventana, bloques. |
| `test_ack_sender.py` | Emisor SACK: ventana, ACK nuevo/viejo/duplicado, fast retransmit, marcas SACK, timeout. |
| `test_sack_ok.py` | SACK sin fallos, en las dos direcciones: tamanios, forma de los segmentos, ventana. |
| `test_sack_un_fallo.py` | Una perdida (primero/medio/ultimo), un ACK perdido, duplicado, desorden, handshake. |
| `test_sack_rafagas.py` | Fallos lineales: rafagas de 2, CWND y mas; el mismo segmento varias veces; ACKs seguidos. |
| `test_sack_disperso.py` | Fallos no lineales: perdidas salteadas, aleatoria con semilla (10% y 30%), mezclas. |
| `test_sack_timeout.py` | Retransmision por timeout: cuando salta, a los cuantos ms, y reenvios espurios. |
| `test_sack_timeout_perdida.py` | Timeout + perdida: doble timeout, fast retransmit perdido, agotamiento. |
| `test_sack_errores.py` | ERR remoto, trafico ajeno, datagramas invalidos, limite de silencio de `recv()` (armado con el primer dato), bordes y cierre. |
| `test_cambio_de_sentido.py` | Cambio de sentido en una conexion (pedido -> respuesta -> archivo) perdiendo el ACK justo antes del cambio, en SW y SACK. |
| `test_limite_tamanio.py` | Limite de `MAX_FILE_SIZE` del upload: mensajes `UPLOAD <tamanio> <nombre>` / `OK` / `ERROR`, bordes del limite, rechazo de punta a punta. |
| `test_sack_cancel.py` | Cancelacion en SACK: `cancel()`, ERR+CANCEL, `shutdown()` entre extremos, `close()` concurrente, upload cancelado. |

Los tests de SACK heredan de `SACKTestCase` (en `base.py`), que agrega el
transporte armado a mano (`transporte_a_mano`), las dos direcciones de una
conexion (`direcciones`) y la verificacion de la ventana
(`assertVentanaRespetada`). `netsim.py` suma politicas especificas de SACK
(`drop_seq`, `drop_seqs`, `drop_acks_sack_nth`, `delay_seq`, `dup_seq`,
`todas`), porque en SACK los datos tambien llevan el flag ACK.

Los tests marcados `HALLAZGO (sin arreglar)` documentan un defecto: no
prueban que el codigo este bien, prueban que el defecto existe y es
reproducible. Si se arregla, hay que darlos vuelta.

## Escala de tiempo

`TEST_TIMEOUT = 0.05` y `TEST_RETRIES = 4` en `base.py`. Los tests de SACK que
cuentan retransmisiones exactas usan `SACK_TIMEOUT_EXACTO = 0.2`: con varios
segmentos en vuelo, una demora del scheduler mayor al timeout dispara
reenvios espurios con la maquina cargada. Algunas clases suben
`retries` porque esperan varios timeouts a proposito (`recv()` abandona tras
`RETRIES` timeouts seguidos); esta anotado en cada una.
