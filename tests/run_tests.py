#!/usr/bin/env python3
"""Corre la suite de Stop & Wait y (opcional) reporta cobertura de lineas.

    python3 tests/run_tests.py              # solo tests
    python3 tests/run_tests.py --cobertura  # tests + lineas no ejecutadas

Usa unittest y trace de la biblioteca estandar: no hace falta instalar nada.
"""

import os
import sys
import threading
import trace
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
SRC = os.path.join(RAIZ, "src")
for ruta in (SRC, AQUI):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

OBJETIVOS = [
    os.path.join(SRC, "lib", "protocols", "stop_and_wait", "stop_wait.py"),
    os.path.join(SRC, "lib", "protocols", "packet", "packet.py"),
    os.path.join(SRC, "lib", "protocols", "selective_ack", "sack_option.py"),
    os.path.join(SRC, "lib", "protocols", "selective_ack", "ack_receiver.py"),
    os.path.join(SRC, "lib", "protocols", "selective_ack", "ack_sender.py"),
    os.path.join(SRC, "lib", "protocols", "selective_ack", "selective_ack.py"),
    os.path.join(SRC, "lib", "protocols", "selective_ack", "trace.py"),
]


def correr(verbosidad=2):
    suite = unittest.TestLoader().discover(AQUI, pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=verbosidad)
    return runner.run(suite)


def lineas_ejecutables(ruta):
    """Lineas de codigo real.

    Sin comentarios, docstrings ni continuaciones.
    """
    import dis
    import inspect

    fuente = open(ruta, encoding="utf-8").read()
    codigo = compile(fuente, ruta, "exec")
    encontradas = set()
    pendientes = [codigo]
    while pendientes:
        c = pendientes.pop()
        for _, linea in dis.findlinestarts(c):
            if linea:
                encontradas.add(linea)
        for const in c.co_consts:
            if inspect.iscode(const):
                pendientes.append(const)
    return encontradas


def reporte(contadores):
    print("\n" + "=" * 72)
    print("COBERTURA DE LINEAS")
    print("=" * 72)
    ejecutadas_por_archivo = {}
    for (archivo, linea), n in contadores.counts.items():
        archivo_abs = os.path.abspath(archivo)
        ejecutadas_por_archivo.setdefault(archivo_abs, set()).add(linea)

    for objetivo in OBJETIVOS:
        total = lineas_ejecutables(objetivo)
        ejecutadas = ejecutadas_por_archivo.get(objetivo, set()) & total
        faltantes = sorted(total - ejecutadas)
        pct = 100.0 * len(ejecutadas) / len(total) if total else 100.0
        rel = os.path.relpath(objetivo, RAIZ)
        print(f"\n{rel}: {len(ejecutadas)}/{len(total)} lineas  ({pct:.1f}%)")
        if faltantes:
            fuente = open(objetivo, encoding="utf-8").read().splitlines()
            print("  sin ejecutar:")
            for n in faltantes:
                print(f"    {n:4d}  {fuente[n - 1].strip()}")


def main():
    con_cobertura = "--cobertura" in sys.argv

    if not con_cobertura:
        resultado = correr()
        return 0 if resultado.wasSuccessful() else 1

    tracer = trace.Trace(
        count=1, trace=0, ignoredirs=[sys.prefix, sys.exec_prefix]
    )
    threading.settrace(tracer.globaltrace)  # para los hilos de los tests
    resultado = tracer.runfunc(correr, verbosidad=1)
    threading.settrace(None)
    reporte(tracer.results())
    return 0 if resultado.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
