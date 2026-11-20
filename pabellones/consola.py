"""Utilidades para los scripts de doble clic (INICIAR_PABELLONES.py)."""
import os
import sys


def preparar(titulo):
    """UTF-8 y título en la ventana de consola de Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleTitleW(titulo)
    except Exception:
        pass
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def pausa(texto="\nPresione Enter para cerrar esta ventana…"):
    """Evita que la ventana del doble clic se cierre antes de leerla."""
    try:
        input(texto)
    except (EOFError, KeyboardInterrupt):
        pass
