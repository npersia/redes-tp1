import sys
import os
from lib.factory import TransportFactory

#HECHO CON IA
#NO CUMPLE CON LO QUE SE PIDE EN EL TP, HAY QUE HACERLO DE CERO
#SIRVE PARA PROBAR LOS PROTOCOLOS

def main():
    protocol_name = sys.argv[1] if len(sys.argv) > 1 else "tcp"
    filepath = sys.argv[2] if len(sys.argv) > 2 else "documento.pdf"

    if not os.path.exists(filepath):
        print(f"[Error] El archivo '{filepath}' no existe.")
        return

    # 1. Leer archivo completo a memoria
    with open(filepath, "rb") as file:
        contenido = file.read()

    # 2. Enviar datos completos a la capa de transporte
    client_transport = TransportFactory.get_transport(protocol_name, "127.0.0.1", 8080)
    client_transport.connect()
    print(f"[Cliente] Transmitiendo {len(contenido)} bytes vía {protocol_name}...")

    # La fragmentación en datagramas UDP y el control de errores ocurren dentro de send()
    client_transport.send(contenido)

    print("[Cliente] Transferencia completada.")
    client_transport.close()

if __name__ == "__main__":
    main()