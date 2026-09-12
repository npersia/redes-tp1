import sys
import os
from lib.factory import TransportFactory

#HECHO CON IA
#NO CUMPLE CON LO QUE SE PIDE EN EL TP, HAY QUE HACERLO DE CERO
#SIRVE PARA PROBAR LOS PROTOCOLOS

# Determina la carpeta raíz donde vive server.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILEPATH = os.path.join(BASE_DIR, "recibido.pdf")

def main():
    protocol_name = sys.argv[1] if len(sys.argv) > 1 else "tcp"

    server_transport = TransportFactory.get_transport(protocol_name, "127.0.0.1", 8080)
    server_transport.start_server()
    print(f"[Servidor] Esperando recibir archivo vía {protocol_name}...")

    try:
        connection = server_transport.accept()
        print(f"[Servidor] Cliente conectado. Guardando en '{OUTPUT_FILEPATH}'...")

        contenido_completo = connection.recv()

        with open(OUTPUT_FILEPATH, "wb") as file:
            file.write(contenido_completo)

        print(f"[Servidor] Archivo guardado correctamente ({len(contenido_completo)} bytes).")
        connection.close()
    finally:
        server_transport.close()

if __name__ == "__main__":
    main()