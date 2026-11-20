"""Modo demostración: responde con los valores de ejemplo anonimizados más una variación leve.

Sirve para revisar el dashboard fuera de la red del hospital (python -m pabellones --demo).
No abre sockets: nunca contacta un PLC.
"""
import json
import math
import os
import random
import time

from . import RAIZ

ESCANEO = os.path.join(RAIZ, "herramientas", "escaneo_demo.json")
CORTO = {"analogInput": "AI", "analogOutput": "AO", "analogValue": "AV", "binaryInput": "BI",
         "binaryOutput": "BO", "binaryValue": "BV", "multiStateValue": "MV"}


class ClienteDemo:
    _valores = None

    def __init__(self):
        if ClienteDemo._valores is None:
            with open(ESCANEO, encoding="utf-8") as f:
                escaneo = json.load(f)
            ClienteDemo._valores = {
                (ip, o["t"], o["i"]): o.get("pv")
                for ip, d in escaneo["equipos"].items() for o in d.get("objetos", [])}

    def leer(self, ip, tipo, inst, propiedad="present-value"):
        v = self._valores.get((ip, CORTO.get(tipo, tipo), inst))
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return v
        if CORTO.get(tipo) in ("AI", "AV") and isinstance(v, float) and abs(v) > 0.5:
            onda = math.sin(time.time() / 600 + inst) * 0.004
            return round(v * (1 + onda + random.uniform(-0.002, 0.002)), 3)
        return v

    def cerrar(self):
        pass
