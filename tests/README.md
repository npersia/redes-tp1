# Suite de pruebas de Stop & Wait

129 tests, sin dependencias externas (solo `unittest` y `trace` de la stdlib).

```bash
python3 tests/run_tests.py              # correr todo
python3 tests/run_tests.py --cobertura  # + reporte de lineas no ejecutadas
python3 -m unittest test_send -v        # un modulo suelto (desde tests/)
```

Cobertura actual: **100% de las lineas** de `stop_wait.py` y de `packet.py`.

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

Los tests marcados `HALLAZGO (sin arreglar)` documentan un defecto: no
prueban que el codigo este bien, prueban que el defecto existe y es
reproducible. Si se arregla, hay que darlos vuelta.

## Escala de tiempo

`TEST_TIMEOUT = 0.05` y `TEST_RETRIES = 4` en `base.py`. Algunas clases suben
`retries` porque esperan varios timeouts a proposito (`recv()` abandona tras
`RETRIES` timeouts seguidos); esta anotado en cada una.
