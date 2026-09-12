#!/bin/bash

#HECHO CON IA
#NO ES PARTE DEL TP


SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

FILE1="${1:-$ROOT_DIR/documento.pdf}"
FILE2="${2:-$ROOT_DIR/recibido.pdf}"

if [ ! -f "$FILE1" ] || [ ! -f "$FILE2" ]; then
    echo "FALTAN ARCHIVOS ($FILE1, $FILE2)."
    exit 2
fi

if diff -q "$FILE1" "$FILE2" > /dev/null 2>&1; then
    echo "OK"
    exit 0
else
    echo "ERROR"
    exit 1
fi