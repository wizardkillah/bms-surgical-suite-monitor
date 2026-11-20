"""Dashboard de Pabellones Quirúrgicos y salas de parto — Piso 2, Edificio A.

Hospital Demo. Tableros TCC-Q2-01, Q2-02, Q2-03 y Q2-04 (8 controladores
Sauter, 192.0.2.21–.28). Solo lectura (BACnet ReadProperty): nunca escribe en un PLC.
"""
import os
import sys

__version__ = "1.1.0"

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_WEB = os.path.join(RAIZ, "web")
DIR_CONFIG = os.path.join(RAIZ, "config")
DIR_DATOS = os.path.join(RAIZ, "datos")
DIR_LOGS = os.path.join(RAIZ, "logs")
DIR_VENDOR = os.path.join(RAIZ, "vendor")

# openpyxl empaquetado: el PC del hospital no necesita pip ni internet.
if os.path.isdir(DIR_VENDOR) and DIR_VENDOR not in sys.path:
    sys.path.insert(0, DIR_VENDOR)
