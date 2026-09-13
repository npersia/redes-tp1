#!/bin/bash

#HECHO CON IA
#NO ES PARTE DEL TP

# Ubicación del script y de la raíz del proyecto
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Nos movemos a la raíz del proyecto para mantener el contexto relativo
cd "$ROOT_DIR" || exit 1

FILE="${1:-$ROOT_DIR/Tp1-Consigna.pdf}"

if [ ! -f "$FILE" ]; then
    echo "❌ Error: No se encontró el archivo '$FILE'."
    exit 1
fi

echo "🚀 Iniciando servidor en segundo plano..."
python3 src/start-server -s "$ROOT_DIR" &
SERVER_PID=$!

# Mata el proceso del servidor automáticamente al terminar el script
trap "kill $SERVER_PID 2>/dev/null" EXIT

# Pausa para dar tiempo al servidor a hacer el bind del socket
sleep 1

echo "📤 Ejecutando cliente para enviar '$FILE'..."
python3 src/upload -s "$FILE"

# Espera a que finalicen las tareas pendientes
wait $SERVER_PID 2>/dev/null
echo "🏁 Ejecución completada."