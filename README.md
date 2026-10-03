# Redes-TP1

TP1 de Redes: transferencia de archivos sobre UDP con protocolo RDT (Stop & Wait y Selective ACK).

----

## Requisitos

- Python 3

----

## Ejecución

Desde el directorio `src/`:

Terminal 1: Servidor

```text
# Servidor
python3 start-server -H ADDR -p PORT -s DIRPATH
```

Terminal 2: Cliente

```text
# Subir un archivo
python3 upload -H ADDR -p PORT -s FILEPATH -n FILENAME -r protocol

# Bajar un archivo
python3 download -H ADDR -p PORT -d FILEPATH -n FILENAME -r protocol
```

----

## Testeo de la red (Mininet)

Como se ejecutan las redes de prueba:

- Topología básica con 2 host y 3 routers:

  ```text
  sudo mn --custom src/topologias/topologiaBasica.py --topo TopologiaBasica
  ```

   ```text
  sudo mn --custom src/topologias/topologiaBasicon.py --topo TopologiaBasicon
  ```

- Topología con tráfico simulado (todavía no tiene tráfico):

  ```text
  sudo mn --custom src/topologias/topologiaTrafico.py --topo TopologiaTrafico
  ```

python3 src/start-server -H 0.0.0.0 -v -s ./server/storage 
Pasos para abrir wireshark con mininet:
1)  abrir una topologia con mininet
2) usar xtrem h1 
3) wireshark &
4) usas el comando que quieras

* UPLOAD - red punto a punto con 0% de perdida y 40ms de RTT
* UPLOAD - red punto a punto con 10% de perdida y 40ms de RTT - SACK : 56s
* DOWNLOAD - red punto a punto con 10% de perdida y 40ms de RTT - SACK : 57s
* 
