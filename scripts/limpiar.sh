#!/bin/bash

#HECHO CON IA
#NO ES PARTE DEL TP


# Ubicación del script y de la raíz del proyecto
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🧹 Buscando archivos .pdf para eliminar en '$ROOT_DIR'..."

# Buscar archivos .pdf
PDF_FILES=$(find "$ROOT_DIR" -type f -name "*.pdf")

if [ -z "$PDF_FILES" ]; then
    echo "ℹ️ No se encontraron archivos .pdf."
    exit 0
fi

# Eliminar cada archivo encontrado
echo "$PDF_FILES" | while read -r file; do
    rm -f "$file"
    echo "🗑️ Eliminado: ${file#$ROOT_DIR/}"
done

echo "✅ Limpieza completada exitosamente."