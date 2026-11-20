"""Sondeo BACnet de los 8 controladores de pabellones, modelo por sala/UMA, alarmas e historial.

Solo lectura: este módulo nunca escribe en un PLC.
"""
import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import DIR_CONFIG, DIR_DATOS
from .bacnet import ClienteBacnet

log = logging.getLogger("pabellones.monitor")

ARCHIVO_CFG = os.path.join(DIR_CONFIG, "pabellones.json")
ARCHIVO_PUNTOS = os.path.join(DIR_CONFIG, "puntos.json")
ARCHIVO_DB = os.path.join(DIR_DATOS, "pabellones.db")
MANDOS = {1: "MANUAL OFF", 2: "MANUAL ON", 3: "AUTOMÁTICO"}
ORDEN = {"alarma": 0, "aviso": 1, "info": 2}
GASES = ("O2", "AIRE", "VACIO", "N2")
NOMBRE_GAS = {"O2": "oxígeno", "AIRE": "aire medicinal", "VACIO": "vacío", "N2": "nitrógeno"}
FILTROS = ("FILTRO_G4", "FILTRO_F8", "FILTRO_H13")


def _r(v, d=1):
    return None if v is None else round(v, d)


class Monitor:
    def __init__(self, ruta_db=ARCHIVO_DB, cfg=None, puntos=None, fabrica_cliente=None):
        if cfg is None:
            with open(ARCHIVO_CFG, encoding="utf-8") as f:
                cfg = json.load(f)
        if puntos is None:
            with open(ARCHIVO_PUNTOS, encoding="utf-8") as f:
                puntos = json.load(f)
        self.cfg, self.puntos = cfg, puntos
        self.por_clave = {p["clave"]: p for p in puntos}
        self.por_ip = {}
        for p in puntos:
            self.por_ip.setdefault(p["ip"], []).append(p)
        self.uma_senal = {}
        for p in puntos:
            if p["grupo"] == "uma":
                self.uma_senal[(p["uma"], p["senal"])] = p["clave"]
        self._ips_por_ambito = {}
        for p in reversed(puntos):  # el primero (controlador preferido) gana
            for campo in ("uma", "tdg", "ups", "equipo", "scar"):
                if p.get(campo):
                    self._ips_por_ambito[p[campo]] = p["ip"]
        for s in cfg["salas"]:
            self._ips_por_ambito[s["id"]] = self._ips_por_ambito.get(s["uma"])
        self.controladores = cfg["controladores"]
        self.intervalo = int(cfg.get("intervalo_seg", 30))
        self.guardar_cada = int(cfg.get("guardar_cada_seg", 300))
        self.retencion_dias = int(cfg.get("retencion_dias", 365))
        self.lim = cfg.get("limites", {})
        self._fabrica = fabrica_cliente or (lambda: ClienteBacnet(
            cfg.get("ip_local") or None, float(cfg.get("timeout_seg", 0.8))))

        self._lock = threading.RLock()
        self._parar = threading.Event()
        self._hilo = None
        self.cache = {}                 # clave -> (valor, ts)
        self.en_linea = {}              # ip -> bool
        self.ultima_ok = {}             # ip -> ts
        self.sin_respuesta = {}         # ip -> puntos sin respuesta en el último ciclo
        self.ts_ciclo = None
        self.duracion_ciclo = None
        self._ultimo_guardado = 0
        self._contadores = {}
        self.activas = {}
        self._linea_previa = {}

        if ruta_db != ":memory:":
            os.makedirs(os.path.dirname(ruta_db), exist_ok=True)
        self.db = sqlite3.connect(ruta_db, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS lecturas (
                ts INTEGER NOT NULL, clave TEXT NOT NULL, valor REAL,
                PRIMARY KEY (clave, ts)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
                nivel TEXT, tipo TEXT, ambito TEXT, mensaje TEXT, valor TEXT);
            CREATE INDEX IF NOT EXISTS ix_eventos_ts ON eventos(ts);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
        """)
        self.db.commit()
        self._cargar_cache()

    # ------------------------------------------------------------------ db

    def _cargar_cache(self):
        """Última lectura guardada de cada punto: se muestra (marcada) mientras no haya comunicación."""
        with self._lock:
            for clave, valor, ts in self.db.execute(
                    "SELECT clave, valor, MAX(ts) FROM lecturas GROUP BY clave"):
                if clave in self.por_clave:
                    self.cache[clave] = (valor, ts)

    def _guardar(self, ts):
        filas = [(ts, c, v) for c, (v, t) in self.cache.items()
                 if t == ts and v is not None and self.por_clave[c].get("hist")]
        with self._lock:
            self.db.executemany("INSERT OR REPLACE INTO lecturas VALUES (?,?,?)", filas)
            self.db.commit()
        self._ultimo_guardado = ts

    def purgar(self):
        limite = int(time.time()) - self.retencion_dias * 86400
        with self._lock:
            n = self.db.execute("DELETE FROM lecturas WHERE ts < ?", (limite,)).rowcount
            self.db.execute("DELETE FROM eventos WHERE ts < ?", (limite,))
            self.db.commit()
        return n

    def evento(self, nivel, tipo, ambito, mensaje, valor=None, ts=None):
        with self._lock:
            self.db.execute("INSERT INTO eventos (ts, nivel, tipo, ambito, mensaje, valor) "
                            "VALUES (?,?,?,?,?,?)",
                            (int(ts or time.time()), nivel, tipo, ambito, mensaje,
                             None if valor is None else str(valor)))
            self.db.commit()
        log.log(logging.WARNING if nivel == "alarma" else logging.INFO, "[%s] %s", nivel.upper(), mensaje)

    # -------------------------------------------------------------- sondeo

    def _leer_controlador(self, ip):
        """Lee todos los puntos de un controlador con su propio cliente (no son thread-safe)."""
        puntos = self.por_ip.get(ip, [])
        cliente = self._fabrica()
        leidos = {}
        try:
            if not puntos:
                # Controlador sin puntos mapeados (Q2-03-525-2): solo se comprueba si responde.
                return ip, cliente.leer(ip, "device", 4194303, "object-name") is not None, {}
            # Si los 3 primeros no responden, el controlador está caído: no esperar 300 timeouts.
            for p in puntos[:3]:
                leidos[p["clave"]] = self._leer(cliente, p)
                if leidos[p["clave"]] is not None:
                    break
            if not any(v is not None for v in leidos.values()):
                return ip, False, {}
            for p in puntos:
                if p["clave"] not in leidos:
                    leidos[p["clave"]] = self._leer(cliente, p)
        finally:
            cliente.cerrar()
        return ip, True, leidos

    @staticmethod
    def _leer(cliente, p):
        v = cliente.leer(p["ip"], p["tipo"], p["inst"])
        if isinstance(v, bool):
            v = int(v)
        return v if isinstance(v, (int, float)) else None

    def ciclo(self):
        t0 = time.time()
        ts = int(t0)
        ips = [c["ip"] for c in self.controladores]
        with ThreadPoolExecutor(max_workers=len(ips) or 1) as ex:
            resultados = list(ex.map(self._leer_controlador, ips))
        with self._lock:
            self.ts_ciclo = ts
            self.duracion_ciclo = round(time.time() - t0, 2)
            for ip, ok, leidos in resultados:
                self.en_linea[ip] = ok
                if ok:
                    self.ultima_ok[ip] = ts
                    self.sin_respuesta[ip] = sum(1 for v in leidos.values() if v is None)
                    for clave, v in leidos.items():
                        if v is not None:
                            self.cache[clave] = (float(v), ts)
            self._evaluar(ts)
        if any(ok for _, ok, _ in resultados) and ts - self._ultimo_guardado >= self.guardar_cada:
            self._guardar(ts)
        return sum(1 for _, ok, _ in resultados if ok)

    def _bucle(self):
        while not self._parar.wait(self.intervalo):
            try:
                self.ciclo()
                if int(time.time()) % 86400 < self.intervalo:
                    self.purgar()
            except Exception:
                log.exception("error en el ciclo de sondeo")

    def arrancar(self):
        self._hilo = threading.Thread(target=self._bucle, name="sondeo", daemon=True)
        self._hilo.start()

    def detener(self):
        self._parar.set()
        if self._hilo:
            self._hilo.join(timeout=5)

    # ------------------------------------------------------------- valores

    def valor(self, clave, fresco=False):
        """Valor de un punto. Con fresco=True solo si se leyó en el último ciclo."""
        if not clave or clave not in self.cache:
            return None
        v, ts = self.cache[clave]
        if fresco and ts != self.ts_ciclo:
            return None
        return v

    def _u(self, uma, senal, fresco=False):
        return self.valor(self.uma_senal.get((uma, senal)), fresco)

    def _plausible(self, magnitud, v):
        lo, hi = self.lim.get("plausible", {}).get(magnitud, (-1e9, 1e9))
        return v is not None and lo <= v <= hi

    def _medida(self, magnitud, clave, dec=1):
        """{valor, estado: ok|sensor|sin_dato, viejo}: separa un sensor dañado de un dato real."""
        v = self.valor(clave)
        if v is None:
            return {"valor": None, "estado": "sin_dato", "viejo": True}
        viejo = self.cache[clave][1] != self.ts_ciclo
        if not self._plausible(magnitud, v):
            return {"valor": _r(v, dec), "estado": "sensor", "viejo": viejo}
        return {"valor": _r(v, dec), "estado": "ok", "viejo": viejo}

    # -------------------------------------------------------------- modelo

    def uma(self, uma):
        g = lambda s: self._u(uma, s)  # noqa: E731
        claves = [c for (u, _), c in self.uma_senal.items() if u == uma]
        ip = self.por_clave[claves[0]]["ip"] if claves else None
        mando = g("MANDO_GENERAL")
        inyeccion = g("EST_VDF_INY")
        if inyeccion is None:
            inyeccion = g("EST_INY")
        filtros = {f[7:]: g(f) for f in FILTROS if (uma, f) in self.uma_senal}
        fallas = [n for s, n in (("FALLA_FUNC_INY", "falla de funcionamiento inyección"),
                                 ("FALLA_FUNC_RET", "falla de funcionamiento retorno"),
                                 ("FALLA_VDF_INY", "falla variador inyección"),
                                 ("FALLA_VDF_RET", "falla variador retorno")) if g(s) == 1]
        if mando == 1 or (g("ON_INY") == 0 and inyeccion != 1):
            estado = "detenida"
        elif fallas:
            estado = "falla"
        elif inyeccion == 1 or (g("MOD_VDF_INY") or 0) > 5:
            estado = "marcha"
        else:
            estado = "sin_confirmar"
        return {
            "id": uma, "ip": ip, "en_linea": bool(ip and self.en_linea.get(ip)),
            "estado": estado, "mando": MANDOS.get(int(mando)) if mando is not None else None,
            "mando_iny": MANDOS.get(int(g("MANDO_INY"))) if g("MANDO_INY") is not None else None,
            "mando_ret": MANDOS.get(int(g("MANDO_RET"))) if g("MANDO_RET") is not None else None,
            "horario": g("HORARIO"), "fallas": fallas,
            "filtros": filtros,
            "inyeccion": {"marcha": inyeccion, "orden": g("ON_INY"), "vdf": _r(g("MOD_VDF_INY")),
                          "sdp": _r(g("SDP_INY"), 0), "sdp_sp": _r(g("SP_VDF_INY"), 0),
                          "moa": g("EST_MOA_INY"), "horas": _r(g("HORAS_INY"), 0)},
            "retorno": {"marcha": g("EST_VDF_RET") if g("EST_VDF_RET") is not None else g("EST_RET"),
                        "orden": g("ON_RET"), "vdf": _r(g("MOD_VDF_RET")),
                        "sdp": _r(g("SDP_RET"), 0), "sdp_sp": _r(g("SP_VDF_RET"), 0),
                        "moa": g("EST_MOA_RET"), "horas": _r(g("HORAS_RET"), 0)},
            "humectador": {"orden": g("ON_HUM"), "estado": g("EST_HUM"), "horas": _r(g("HORAS_HUM"), 0)},
            "aire": {"mezcla": self._medida("temp", self.uma_senal.get((uma, "TEMP_MEZCLA"))),
                     "inyeccion": self._medida("temp", self.uma_senal.get((uma, "TEMP_INY"))),
                     "retorno": self._medida("temp", self.uma_senal.get((uma, "TEMP_RET")))},
            "agua_fria": {"iny": self._medida("temp", self.uma_senal.get((uma, "TEMP_AF_INY"))),
                          "ret": self._medida("temp", self.uma_senal.get((uma, "TEMP_AF_RET"))),
                          "valvula": _r(g("MOD_VAL_FRIO")), "flujo": g("FLUJO_FRIO")},
            "agua_caliente": {"iny": self._medida("temp", self.uma_senal.get((uma, "TEMP_AC_INY"))),
                              "ret": self._medida("temp", self.uma_senal.get((uma, "TEMP_AC_RET"))),
                              "valvula": _r(g("MOD_VAL_CALOR")), "flujo": g("FLUJO_CALOR")},
            "co2": _r(g("CO2"), 0), "temp_exterior": _r(g("TEMP_EXT")),
            "claves": {s: c for (u, s), c in self.uma_senal.items() if u == uma},
        }

    def sala(self, s):
        uma = s["uma"]
        k = lambda senal: self.uma_senal.get((uma, senal))  # noqa: E731
        sala_pt = {p["senal"]: p["clave"] for p in self.puntos
                   if p["grupo"] == "sala" and p.get("sala") == s["id"]}
        clave_t = k("TEMP_AMB") or k("TEMP_SALA") or sala_pt.get("TEMP")
        clave_h = k("HUM_AMB") or sala_pt.get("HUM")
        clave_p = k("PRES_AMB")
        lim = self.lim.get(s["tipo"], {})
        return {
            **{x: s.get(x) for x in ("id", "nombre", "uma", "tipo", "nota")},
            "temp": {**self._medida("temp", clave_t), "sp": _r(self._u(uma, "TEMP_SP")),
                     "banda": _r(self._u(uma, "TEMP_BANDA")), "lim": lim.get("temp"), "clave": clave_t},
            "hum": {**self._medida("hum", clave_h, 0), "sp": _r(self._u(uma, "HUM_SP"), 0),
                    "lim": lim.get("hum"), "clave": clave_h},
            "pres": {**self._medida("pres", clave_p), "sp": _r(self._u(uma, "PRES_SP")),
                     "min": lim.get("pres_min"), "clave": clave_p} if clave_p else None,
        }

    def apoyo(self):
        def de(grupo, campo):
            out = {}
            for p in self.puntos:
                if p["grupo"] == grupo:
                    out.setdefault(p.get(campo, ""), {})[p["senal"]] = self.valor(p["clave"])
            return out
        circ = [p for p in self.puntos if p["grupo"] == "circuito"]
        ambiente = []
        for p in self.puntos:
            if p["grupo"] != "ambiente":
                continue
            s = p["senal"]
            mag = "co2" if s.startswith("CO2") else "hum" if "HUM" in s else "pres" if "PRES" in s else "temp"
            ambiente.append({"nombre": s.replace("_", " "), "mag": mag, "unidad": p.get("unidad", ""),
                             "clave": p["clave"], **self._medida(mag, p["clave"], 0 if mag == "co2" else 1)})
        scar = de("scar", "scar")
        return {
            "gases": de("gas", "tdg"), "ups": de("ups", "ups"),
            "precision": de("precision", "equipo"), "ambiente": ambiente,
            "scar": {k: {**v, "medida": self._medida("temp", next(
                (p["clave"] for p in self.puntos if p["grupo"] == "scar" and p["scar"] == k
                 and p["senal"] == "TEMP"), None))} for k, v in scar.items()},
            "extractor": de("extractor", "equipo"), "equipos_l": de("equipo_l", "equipo"),
            "circuitos": {"total": len(circ),
                          "encendidos": sum(1 for p in circ if self.valor(p["clave"]) == 1),
                          "detalle": {p["senal"]: self.valor(p["clave"]) for p in circ}},
        }

    # ------------------------------------------------------------- alarmas

    def _condiciones(self):
        """[(id, nivel, ámbito, mensaje, valor)] que se cumplen con datos de este ciclo."""
        out = []
        f = lambda c: self.valor(c, fresco=True)  # noqa: E731
        for s in self.cfg["salas"]:
            sala = self.sala(s)
            n, lim = s["nombre"], self.lim.get(s["tipo"], {})
            for mag, txt, unidad in (("temp", "Temperatura", "°C"), ("hum", "Humedad", "%")):
                m = sala[mag]
                v = f(m["clave"])
                if v is None:
                    continue
                if not self._plausible(mag, v):
                    out.append((f"{s['id']}-{mag}-sensor", "aviso", n, f"{n}: sensor de {txt.lower()} fuera de rango", _r(v)))
                elif lim.get(mag):
                    lo, hi = lim[mag]
                    if not lo <= v <= hi:
                        lejos = v < lo - 2 or v > hi + 2 if mag == "temp" else False
                        out.append((f"{s['id']}-{mag}", "alarma" if lejos else "aviso", n,
                                    f"{n}: {txt.lower()} {_r(v)} {unidad} fuera del rango {lo:g}–{hi:g} {unidad}", _r(v)))
            if sala["pres"] and lim.get("pres_min") is not None:
                v = f(sala["pres"]["clave"])
                if v is not None and self._plausible("pres", v) and v < lim["pres_min"]:
                    out.append((f"{s['id']}-pres", "aviso", n,
                                f"{n}: presión de sala {_r(v)} Pa, bajo el mínimo de {lim['pres_min']:g} Pa", _r(v)))
                elif v is not None and not self._plausible("pres", v):
                    out.append((f"{s['id']}-pres-sensor", "aviso", n, f"{n}: sensor de presión fuera de rango", _r(v)))
        nombres_sala = {s["uma"]: s["nombre"] for s in self.cfg["salas"]}
        for uma in sorted({u for u, _ in self.uma_senal}, key=lambda x: int(x.split(".")[1])):
            info = self.uma(uma)
            if not info["en_linea"]:
                continue
            donde = f"{nombres_sala[uma]} · UMA {uma}" if uma in nombres_sala else f"UMA {uma}"
            if info["mando"] == "MANUAL OFF":
                out.append((f"{uma}-off", "aviso", donde, f"{donde}: detenida en MANUAL OFF", None))
            elif info["mando"] == "MANUAL ON":
                out.append((f"{uma}-manual", "info", donde, f"{donde}: en MANUAL ON (sin horario automático)", None))
            for falla in info["fallas"]:
                out.append((f"{uma}-{falla}", "alarma", donde, f"{donde}: {falla}", None))
            for filtro, v in info["filtros"].items():
                if v == 1:
                    out.append((f"{uma}-filtro-{filtro}", "aviso", donde, f"{donde}: filtro {filtro} sucio", None))
            if info["estado"] == "marcha":
                for tramo, nombre in (("agua_fria", "agua fría"), ("agua_caliente", "agua caliente")):
                    if info[tramo]["flujo"] == 0:
                        out.append((f"{uma}-flujo-{tramo}", "aviso", donde, f"{donde}: sin flujo en serpentín de {nombre}", None))
            iny = info["inyeccion"]
            if iny["orden"] == 1 and iny["marcha"] == 0 and (iny["vdf"] or 0) < 5:
                out.append((f"{uma}-iny-sin-marcha", "alarma", donde, f"{donde}: inyección con orden de marcha y detenida", None))
        apoyo = self.apoyo()
        for tdg, gases in apoyo["gases"].items():
            for gas, v in gases.items():
                if v == 1:
                    out.append((f"gas-{tdg}-{gas}", "alarma", "Gases clínicos",
                                f"Gases clínicos {tdg}: alarma de {NOMBRE_GAS.get(gas, gas)}", None))
        bypass = []
        for ups, s in apoyo["ups"].items():
            for senal, nivel, txt in (("GENERAL", "alarma", "alarma general"), ("BATERIA", "alarma", "alarma de batería"),
                                      ("SOBRECARGA", "alarma", "sobrecarga"), ("AUTO", "aviso", "alarma AUTO")):
                if s.get(senal) == 1:
                    out.append((f"{ups}-{senal}", nivel, "UPS", f"{ups}: {txt}", None))
            if s.get("BYPASS") == 1:
                bypass.append(ups)
        if bypass:
            out.append(("ups-bypass", "aviso", "UPS",
                        f"{len(bypass)} de {len(apoyo['ups'])} UPS reportan bypass ({', '.join(bypass)})", len(bypass)))
        for eq, s in apoyo["equipos_l"].items():
            for senal, txt in (("AVERIA", "avería"), ("FUERA_SERVICIO", "fuera de servicio")):
                if s.get(senal) == 1:
                    out.append((f"{eq}-{senal}", "aviso", f"Equipo {eq}", f"Equipo {eq}: {txt}", None))
        for eq, s in apoyo["extractor"].items():
            for senal, txt in (("TERMICO", "térmico activado"), ("PRESIÓN", "falla de presión")):
                if s.get(senal) == 1:
                    out.append((f"{eq}-{senal}", "aviso", eq, f"Extractor {eq}: {txt}", None))
        for eq, s in apoyo["precision"].items():
            if s.get("ALARMA_GENERAL") == 1:
                out.append((f"{eq}-general", "aviso", eq, f"Equipo de precisión {eq}: alarma general", None))
        co2 = self.lim.get("co2_aviso")
        for a in apoyo["ambiente"]:
            if a["mag"] == "co2" and a["estado"] == "ok" and not a["viejo"] and co2 and a["valor"] > co2:
                out.append((f"amb-{a['clave']}", "aviso", "Ambiente", f"{a['nombre']}: {a['valor']:g} ppm", a["valor"]))
        return out

    def _ip_de_alarma(self, aid):
        """Controlador del que depende una alarma (prefijo más largo: UMA, sala, TDG, UPS…)."""
        if aid.startswith("amb-"):
            return self.por_clave.get(aid[4:], {}).get("ip")
        aid = aid[4:] if aid.startswith("gas-") else aid
        mejor = max((t for t in self._ips_por_ambito if aid.startswith(t + "-")), key=len, default=None)
        return self._ips_por_ambito.get(mejor)

    def _evaluar(self, ts):
        nuevas = {}
        for c in self.controladores:
            ip, linea = c["ip"], self.en_linea.get(c["ip"])
            previa = self._linea_previa.get(ip)
            if previa is not None and previa != linea:
                self.evento("info" if linea else "alarma", "COMUNICACION", c["id"],
                            f"{c['id']} ({ip}): comunicación {'restablecida' if linea else 'perdida'}", ts=ts)
            self._linea_previa[ip] = linea
            if linea is False:
                aid = f"com-{ip}"
                nuevas[aid] = {"id": aid, "nivel": "alarma" if "UMA" in c.get("rol", "")[:3] else "aviso",
                               "ambito": c["id"], "valor": None,
                               "mensaje": f"{c['id']} ({ip}) sin respuesta BACnet",
                               "desde": self.activas.get(aid, {}).get("desde", ts)}
        vistas = set()
        for aid, nivel, ambito, msg, v in self._condiciones():
            vistas.add(aid)
            self._contadores[aid] = self._contadores.get(aid, 0) + 1
            if self._contadores[aid] < 2 and aid not in self.activas:
                continue                    # confirmar en un segundo ciclo
            previa = self.activas.get(aid)
            nuevas[aid] = {"id": aid, "nivel": nivel, "ambito": ambito, "mensaje": msg, "valor": v,
                           "desde": previa["desde"] if previa else ts}
            if not previa:
                self.evento(nivel, "ACTIVA", ambito, msg, v, ts)
        for aid in list(self._contadores):
            if aid not in vistas:
                del self._contadores[aid]
        for aid, a in self.activas.items():
            if aid not in nuevas and not aid.startswith("com-"):
                # Si el controlador cayó, no se sabe si se normalizó: se mantiene congelada.
                ip = self._ip_de_alarma(aid)
                if ip and not self.en_linea.get(ip):
                    nuevas[aid] = a
                    continue
                self.evento("info", "NORMALIZADA", a["ambito"], f"Normalizado: {a['mensaje']}", ts=ts)
        self.activas = nuevas

    # ------------------------------------------------------------ consultas

    def instantanea(self):
        with self._lock:
            salas = [self.sala(s) for s in self.cfg["salas"]]
            umas = {u: self.uma(u) for u in sorted({u for u, _ in self.uma_senal},
                                                   key=lambda x: int(x.split(".")[1]))}
            for u, info in umas.items():
                info["sala"] = next((s["nombre"] for s in self.cfg["salas"] if s["uma"] == u),
                                    self.cfg.get("umas_sin_sala", {}).get(u))
            ctrl = []
            for c in self.controladores:
                ip = c["ip"]
                ctrl.append({**c, "en_linea": self.en_linea.get(ip), "ultima_ok": self.ultima_ok.get(ip),
                             "puntos": len(self.por_ip.get(ip, [])),
                             "sin_respuesta": self.sin_respuesta.get(ip)})
            ts_datos = max((t for _, t in self.cache.values() if t), default=None)
            return {
                "ts": self.ts_ciclo, "duracion_ciclo": self.duracion_ciclo, "intervalo_seg": self.intervalo,
                "ts_datos": ts_datos, "controladores": ctrl, "salas": salas, "umas": umas,
                "apoyo": self.apoyo(), "limites": self.lim,
                "alarmas": sorted(self.activas.values(), key=lambda a: (ORDEN.get(a["nivel"], 3), -a["desde"])),
            }

    def tabla_puntos(self):
        """Todos los puntos sondeados con su último valor (explorador)."""
        with self._lock:
            out = []
            for p in self.puntos:
                v, ts = self.cache.get(p["clave"], (None, None))
                fila = {k: p.get(k) for k in ("clave", "ip", "tipo", "inst", "nombre", "grupo", "uma",
                                              "senal", "unidad", "estados", "activo")}
                fila.update(valor=v, ts=ts)
                out.append(fila)
            return out

    def historial(self, desde, hasta, claves, max_puntos=400, max_series=24):
        claves = [c for c in claves if c in self.por_clave][:max_series]
        paso = max(self.guardar_cada, (hasta - desde) // max_puntos or 1)
        series = {}
        with self._lock:
            for c in claves:
                filas = self.db.execute(
                    "SELECT (ts / ?) * ? AS b, AVG(valor), MIN(valor), MAX(valor) FROM lecturas "
                    "WHERE clave=? AND ts BETWEEN ? AND ? GROUP BY b ORDER BY b",
                    (paso, paso, c, desde, hasta)).fetchall()
                series[c] = [[b, round(a, 2), round(mn, 2), round(mx, 2)] for b, a, mn, mx in filas]
        return {"desde": desde, "hasta": hasta, "paso": paso, "series": series}

    def eventos(self, limite=200, desde=None, hasta=None):
        with self._lock:
            filas = self.db.execute("SELECT ts, nivel, tipo, ambito, mensaje, valor FROM eventos "
                                    "WHERE ts BETWEEN ? AND ? ORDER BY ts DESC, id DESC LIMIT ?",
                                    (desde or 0, hasta or 2**62, limite)).fetchall()
        return [dict(zip(("ts", "nivel", "tipo", "ambito", "mensaje", "valor"), f)) for f in filas]

    def estadisticas(self, desde, hasta, rangos=None, validos=None):
        """clave -> {min, prom, max, n, descartados, primero, ultimo, en_rango}.

        validos: {clave: (lo, hi)} rango físico; lo que queda fuera es un sensor dañado y no
        entra en min/prom/max (se cuenta en 'descartados'). rangos: {clave: (lo, hi)} para el
        porcentaje del tiempo dentro del rango configurado."""
        rangos, validos = rangos or {}, validos or {}
        res = {}
        with self._lock:
            filas = self.db.execute(
                "SELECT clave, MIN(valor), AVG(valor), MAX(valor), COUNT(*), MIN(ts), MAX(ts) "
                "FROM lecturas WHERE ts BETWEEN ? AND ? GROUP BY clave", (desde, hasta)).fetchall()
            for c, mn, av, mx, n, t0, t1 in filas:
                res[c] = {"min": mn, "prom": av, "max": mx, "n": n, "descartados": 0,
                          "primero": t0, "ultimo": t1}
            for c, (lo, hi) in validos.items():
                if c not in res:
                    continue
                mn, av, mx, n = self.db.execute(
                    "SELECT MIN(valor), AVG(valor), MAX(valor), COUNT(*) FROM lecturas "
                    "WHERE clave=? AND ts BETWEEN ? AND ? AND valor BETWEEN ? AND ?",
                    (c, desde, hasta, lo, hi)).fetchone()
                res[c].update(min=mn, prom=av, max=mx, descartados=res[c]["n"] - n, n=n)
            for c, (lo, hi) in rangos.items():
                if c in res and lo is not None and hi is not None:
                    dentro = self.db.execute(
                        "SELECT COUNT(*) FROM lecturas WHERE clave=? AND ts BETWEEN ? AND ? "
                        "AND valor BETWEEN ? AND ?", (c, desde, hasta, lo, hi)).fetchone()[0]
                    res[c]["en_rango"] = dentro / res[c]["n"] if res[c]["n"] else None
        return res

    def tabla(self, desde, hasta, paso, claves):
        """{bucket: {clave: promedio}} por paso (para el Excel)."""
        claves = [c for c in claves if c in self.por_clave]
        if not claves:
            return {}
        marcas = ",".join("?" * len(claves))
        with self._lock:
            filas = self.db.execute(
                f"SELECT (ts / ?) * ? AS b, clave, AVG(valor) FROM lecturas WHERE ts BETWEEN ? AND ? "
                f"AND clave IN ({marcas}) GROUP BY b, clave ORDER BY b",
                (paso, paso, desde, hasta, *claves)).fetchall()
        out = {}
        for b, c, v in filas:
            out.setdefault(b, {})[c] = v
        return out
