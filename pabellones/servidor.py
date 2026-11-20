"""Servidor HTTP del dashboard (biblioteca estándar). Solo lectura: no hay POST.

    python -m pabellones [--puerto 8094] [--host 127.0.0.1] [--sin-navegador] [--demo]

API:
    GET /api/estado                           salas, UMA, apoyo, controladores, alarmas
    GET /api/historial?claves=a,b&horas=24    series agregadas
    GET /api/eventos?limite=200
    GET /api/puntos                           todos los puntos sondeados (explorador)
    GET /api/informe?sala=P1&horas=168             informe técnico HTML (sin sala = general)
    GET /api/excel?sala=P1&desde=2026-11-01&hasta=2026-11-16
    GET /api/salud
"""
import json
import logging
import mimetypes
import os
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import DIR_WEB, __version__, monitor

log = logging.getLogger("pabellones.web")

LOGO_SVG = os.path.join(DIR_WEB, "img", "logo_hospital.svg")
HOSTS_LOCALES = {"localhost", "127.0.0.1", "[::1]", "::1"}
MON = None
DEMO = False
_PESADO = threading.Semaphore(2)   # informes y Excel simultáneos


def _fecha(txt):
    txt = (txt or "").strip()
    if not txt:
        return None
    if txt.isdigit():
        return int(txt)
    return int(datetime.fromisoformat(txt).timestamp())


def _rango(q, horas_def=24 * 7):
    """desde/hasta (fecha o fecha-hora ISO) u horas hacia atrás; 'hasta' de solo fecha incluye el día."""
    desde, hasta = _fecha(q.get("desde", [""])[0]), _fecha(q.get("hasta", [""])[0])
    if desde is None:
        hasta = int(time.time())
        desde = hasta - int(max(1.0, min(float(q.get("horas", [str(horas_def)])[0]), 24 * 400)) * 3600)
    elif hasta is None:
        hasta = int(time.time())
    elif len(q.get("hasta", [""])[0].strip()) == 10:
        hasta += 86399
    if hasta <= desde:
        raise ValueError("el rango de fechas está invertido")
    return desde, min(hasta, int(time.time()) + 60)


def _sala(q):
    sala = q.get("sala", [""])[0].strip() or None
    if sala and sala not in {s["id"] for s in MON.cfg["salas"]}:
        raise ValueError("sala desconocida")
    return sala


def _logo():
    try:
        with open(LOGO_SVG, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ('<div class="logo-texto">Hospital Demo'
                '</div>')


class Servidor(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        # SO_REUSEADDR en Windows permite dos procesos en el mismo puerto: se exige exclusividad.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Manejador(BaseHTTPRequestHandler):
    server_version = f"Pabellones/{__version__}"
    sys_version = ""

    def log_message(self, formato, *args):
        pass

    def _host_valido(self):
        """Contra DNS rebinding: con escucha local solo se aceptan nombres locales."""
        if self.server.server_address[0] not in ("127.0.0.1", "::1"):
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        nombre = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        return nombre in HOSTS_LOCALES

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        super().end_headers()

    def _json(self, datos, codigo=200):
        self._bytes(json.dumps(datos, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8", codigo, "no-store")

    def _bytes(self, cuerpo, tipo, codigo=200, cache="no-cache", extra=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _archivo(self, ruta):
        if ruta in ("", "/"):
            ruta = "/index.html"
        destino = os.path.normpath(os.path.join(DIR_WEB, ruta.lstrip("/")))
        if not destino.startswith(DIR_WEB + os.sep) or not os.path.isfile(destino):
            return self._json({"error": "no encontrado"}, 404)
        with open(destino, "rb") as f:
            cuerpo = f.read()
        if destino.endswith("index.html"):
            cuerpo = (cuerpo.decode("utf-8").replace("@@LOGO_SVG@@", _logo())
                      .replace("@@VERSION@@", __version__)).encode("utf-8")
        tipo = mimetypes.guess_type(destino)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo.endswith("javascript"):
            tipo += "; charset=utf-8"
        self._bytes(cuerpo, tipo)

    def do_POST(self):
        self._json({"error": "dashboard de solo lectura"}, 405)

    do_PUT = do_DELETE = do_PATCH = do_POST

    def do_GET(self):
        if not self._host_valido():
            return self._json({"error": "host no permitido"}, 403)
        partes = urllib.parse.urlparse(self.path)
        ruta, q = partes.path, urllib.parse.parse_qs(partes.query)
        try:
            if ruta == "/api/estado":
                return self._json({**MON.instantanea(), "demo": DEMO, "version": __version__})
            if ruta == "/api/historial":
                horas = max(1.0, min(float(q.get("horas", ["24"])[0]), 24 * 90))
                hasta = int(time.time())
                claves = [c for c in q.get("claves", [""])[0].split(",") if c]
                return self._json(MON.historial(hasta - int(horas * 3600), hasta, claves))
            if ruta == "/api/eventos":
                limite = max(1, min(int(q.get("limite", ["200"])[0]), 2000))
                return self._json({"eventos": MON.eventos(limite)})
            if ruta == "/api/puntos":
                return self._json({"puntos": MON.tabla_puntos(), "ts": MON.ts_ciclo})
            if ruta in ("/api/informe", "/api/excel"):
                return self._documento(ruta, q)
            if ruta == "/api/salud":
                return self._json({"version": __version__, "demo": DEMO, "ultimo_ciclo": MON.ts_ciclo,
                                   "duracion_ciclo": MON.duracion_ciclo, "en_linea": MON.en_linea})
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception:
            log.exception("error en %s", ruta)
            return self._json({"error": "error interno"}, 500)
        if not ruta.startswith("/api/"):
            return self._archivo(ruta)
        return self._json({"error": "ruta desconocida"}, 404)


    def _documento(self, ruta, q):
        desde, hasta = _rango(q)
        sala = _sala(q)
        if not _PESADO.acquire(blocking=False):
            return self._json({"error": "se están generando otros documentos; espere unos segundos"}, 429)
        try:
            t0 = time.time()
            if ruta == "/api/informe":
                from . import informe
                cuerpo = informe.generar(MON, desde, hasta, sala).encode("utf-8")
                log.info("informe técnico %s generado en %.1f s", sala or "general", time.time() - t0)
                return self._bytes(cuerpo, "text/html; charset=utf-8", cache="no-store")
            from . import excel            # openpyxl solo si se usa
            datos, nombre = excel.generar(MON, desde, hasta, sala)
            log.info("Excel %s generado en %.1f s (%d kB)", nombre, time.time() - t0, len(datos) // 1024)
            return self._bytes(datos, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               cache="no-store", extra={"Content-Disposition": f'attachment; filename="{nombre}"'})
        finally:
            _PESADO.release()


def main(argv=None):
    import argparse
    from . import registro
    ap = argparse.ArgumentParser(prog="pabellones", description="Dashboard Pabellones Quirúrgicos")
    ap.add_argument("--puerto", type=int, default=8094)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--sin-navegador", action="store_true")
    ap.add_argument("--demo", action="store_true", help="datos de ejemplo, sin consultar PLC")
    args = ap.parse_args(argv)
    registro.configurar()
    url = f"http://localhost:{args.puerto}"

    try:
        servidor = Servidor((args.host, args.puerto), Manejador)
    except OSError:
        log.error("El puerto %d ya está en uso: el dashboard ya está corriendo. Abra %s", args.puerto, url)
        if not args.sin_navegador:
            webbrowser.open(url)
        return 2

    global MON, DEMO
    DEMO = args.demo
    try:
        if DEMO:
            from . import DIR_DATOS
            from .demo import ClienteDemo
            MON = monitor.Monitor(os.path.join(DIR_DATOS, "demo.db"), fabrica_cliente=ClienteDemo)
        else:
            MON = monitor.Monitor()
    except Exception:
        log.exception("No se pudo iniciar el monitor (revise config/pabellones.json y config/puntos.json)")
        servidor.server_close()
        return 1

    MON.purgar()
    log.info("Pabellones v%s · Python %s%s", __version__, sys.version.split()[0], " · MODO DEMO" if DEMO else "")
    log.info("primera lectura de %d controladores (%d puntos)…", len(MON.controladores), len(MON.puntos))
    n = MON.ciclo()
    log.info("%d de %d controladores en línea · ciclo %.1f s", n, len(MON.controladores), MON.duracion_ciclo)
    MON.arrancar()
    log.info("dashboard en %s  (Ctrl+C para detener)", url)
    if not args.sin_navegador:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        log.info("Deteniendo…")
    finally:
        MON.detener()
        servidor.server_close()
    return 0
