"""Log a consola y a logs/pabellones.log (rotativo, 5 × 1 MB)."""
import logging
import os
from logging.handlers import RotatingFileHandler

from . import DIR_LOGS


def configurar(nivel=logging.INFO):
    os.makedirs(DIR_LOGS, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%d-%m-%Y %H:%M:%S")
    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    for h in list(raiz.handlers):
        raiz.removeHandler(h)
    archivo = RotatingFileHandler(os.path.join(DIR_LOGS, "pabellones.log"),
                                  maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    archivo.setFormatter(fmt)
    consola = logging.StreamHandler()
    consola.setFormatter(fmt)
    raiz.addHandler(archivo)
    raiz.addHandler(consola)
