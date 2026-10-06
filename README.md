# Redes-TP1

TP1 de Redes: transferencia de archivos sobre **UDP** con protocolo **RDT**
(*Stop & Wait* y *Selective ACK*).

---

## Requisitos

- Python 3
- Mininet (solo para las pruebas de red)
- xterm 

---

## Ejecución

Todos los comandos se ejecutan desde el directorio `raíz`.

### Terminal 1 — Servidor

```bash
python3 src/start-server -H ADDR -p PORT -s DIRPATH
```

### Terminal 2 — Cliente

```bash
# Subir un archivo
python3 src/upload -H ADDR -p PORT -s FILEPATH -n FILENAME -r protocol

# Bajar un archivo
python3 src/download -H ADDR -p PORT -d FILEPATH -n FILENAME -r protocol
```

* Cada comando puede ejecutarse con el flag `-h` para tener mas informacion sobre las opciones.

### Ejemplo con la topología de pruebas

En las `xterm` de Mininet cada host ve las carpetas del repo:

- `h1`, `h2`, `h3`, `h4` (clientes) → `storage/client/` — de ahí se sube
- `h5` (servidor) → `storage/server/` — ahí se guarda lo que llega

```bash
# En h5 (el servidor): lee de y escribe en storage/server/
python3 src/start-server -H 0.0.0.0 -p 8080 -s storage/server/ -v

# En h1 (el cliente): subir un archivo de storage/client/
python3 src/upload -H 10.0.0.5 -p 8080 -s "storage/client/ARCHIVE" -n NAME -r sack

# En h1 (el cliente): bajar un archivo que está en storage/server/
python3 src/download -H 10.0.0.5 -p 8080 -d "storage/client/NAME" -n ARCHIVE -r sack
```

`ARCHIVE` es el archivo real que viaja (el que está en la carpeta de origen).
`NAME` es el nombre con el que lo guardás.

---

## Testeo de la red (Mininet)

### Topologías disponibles

| Comando | Descripción |
| --- | --- |
| `sudo mn --custom src/topologias/topologiaBasica.py --topo TopologiaBasica` | 2 hosts y 2 caminos posibles entre ellos |
| `sudo mn --custom src/topologias/topologiaBasicon.py --topo TopologiaBasicon` | 2 hosts conectados en punto a punto |
| `sudo mn --custom src/topologias/topologiaTrafico.py --topo TopologiaTrafico` | Topología con tráfico simulado |
| `sudo mn --custom src/topologias/5_host_3_router.py --topo Topologia5Host3Switch` | 5 hosts y 3 switches en cadena (h5 es el servidor) |

### 5 hosts y 3 switches

```bash
# Desde la raíz del proyecto
sudo mn --custom src/topologias/5_host_3_router.py --topo Topologia5Host3Switch

# Si ya estás parado en src/
sudo mn --custom topologias/5_host_3_router.py --topo Topologia5Host3Switch
```

### Capturar tráfico con Wireshark

1. Levantar la topología con Mininet.
2. Entrar a un host: `xterm h1`.
3. Lanzar Wireshark: `wireshark &` o utilizando el plugging para traducir mas facilmente los paquetes `wireshark -X lua_script:wireshark/rdt.lua`
4. Ejecutar el comando que quieras (cliente o servidor).

### Comparación de archivos

Después de cada transferencia, verificá que el archivo recibido sea idéntico
al original. `cmp -s` compara byte a byte y no imprime nada: sale con `0` si
son iguales, por eso el `&& echo OK || echo FALLA`.

```bash
cmp -s storage/client/ARCHIVO storage/server/ARCHIVO && echo OK || echo FALLA
```

Ejemplo:

```bash
cmp -s storage/client/operacion.pdf storage/server/mission && echo OK || echo FALLA
```

---

## Resultados de las pruebas

| Prueba | Red | Tiempo |
| --- | --- | --- |
| UPLOAD | Punto a punto, 0% pérdida, 40ms RTT | — |
| UPLOAD | Punto a punto, 10% pérdida, 40ms RTT — SACK | 56s |
| DOWNLOAD | Punto a punto, 10% pérdida, 40ms RTT — SACK | 57s |
