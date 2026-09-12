#!/bin/bash

#HECHO CON IA
#NO ES PARTE DEL TP

# Ubicación del script y de la raíz del proyecto
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Nos movemos a la raíz del proyecto para mantener el contexto relativo
cd "$ROOT_DIR" || exit 1

PROTOCOL="${1:-tcp}"
FILE="${2:-$ROOT_DIR/documento.pdf}"

if [ ! -f "$FILE" ]; then
    echo "❌ Error: No se encontró el archivo '$FILE'."
    exit 1
fi

echo "🚀 Iniciando servidor en segundo plano con protocolo: $PROTOCOL..."
python3 server.py "$PROTOCOL" &
SERVER_PID=$!

# Mata el proceso del servidor automáticamente al terminar el script
trap "kill $SERVER_PID 2>/dev/null" EXIT

# Pausa para dar tiempo al servidor a hacer el bind del socket
sleep 1

echo "📤 Ejecutando cliente para enviar '$FILE'..."
python3 client.py "$PROTOCOL" "$FILE"

# Espera a que finalicen las tareas pendientes
wait $SERVER_PID 2>/dev/null
echo "🏁 Ejecución completada."