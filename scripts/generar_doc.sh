#!/bin/bash

#HECHO CON IA
#NO ES PARTE DEL TP


# Ubicación del script y de la raíz del proyecto
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Tamaño deseado (5 MB por defecto) o pasado por parámetro (ej: 10M, 1M)
SIZE="${1:-5M}"
OUTPUT_FILE="$ROOT_DIR/documento.pdf"

echo "📄 Generando archivo de prueba en '$OUTPUT_FILE' ($SIZE)..."

# Usa /dev/urandom para crear un archivo binario compatible con Linux y macOS
head -c "$SIZE" /dev/urandom > "$OUTPUT_FILE" 2>/dev/null

# Fallback por si head -c difiere entre entornos
if [ ! -s "$OUTPUT_FILE" ]; then
    dd if=/dev/urandom of="$OUTPUT_FILE" bs=1M count=5 status=none
fi

echo "✅ Archivo 'documento.pdf' generado exitosamente en la raíz del proyecto."