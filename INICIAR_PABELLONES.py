"""Pabellones Quirúrgicos — ARRANQUE. Doble clic en este archivo.

Abre una consola VISIBLE (el antivirus marca los procesos ocultos) y el navegador en
http://localhost:8094. Cerrar la ventana detiene el monitoreo.
Sin .bat, sin PowerShell y sin procesos hijos: un único python.exe.
Para revisar el diseño fuera de la red del hospital: INICIAR_PABELLONES.py --demo
"""
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
os.chdir(AQUI)
sys.path.insert(0, AQUI)
sys.dont_write_bytecode = True

if __name__ == "__main__":
    from pabellones import consola
    consola.preparar("Pabellones Quirúrgicos - Hospital Demo")
    if sys.version_info < (3, 8):
        print("Se requiere Python 3.8 o superior. Versión actual:", sys.version.split()[0])
        consola.pausa()
        sys.exit(1)
    try:
        from pabellones.servidor import main
        codigo = main(sys.argv[1:])
    except Exception:
        import traceback
        traceback.print_exc()
        codigo = 1
    if codigo == 1:
        print("\nEl dashboard se detuvo con error. Revise logs\\pabellones.log")
        consola.pausa()
    elif codigo == 2:
        consola.pausa("\nYa estaba corriendo. Presione Enter para cerrar esta ventana…")
    sys.exit(codigo)
