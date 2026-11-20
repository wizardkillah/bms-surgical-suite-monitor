"""Datos comunes del informe técnico y del Excel: período, estadísticas y observaciones.

Las observaciones se redactan solo con lecturas y reglas configuradas; no sustituyen la
revisión en terreno ni constituyen una certificación normativa.
"""
from datetime import datetime

# Señales de la UMA que se resumen por período (las que guarda el historial).
SENALES_UMA = (("TEMP_INY", "T° aire inyección", "°C", "temp"), ("TEMP_RET", "T° aire retorno", "°C", "temp"),
               ("TEMP_AF_INY", "T° agua fría impulsión", "°C", "temp"),
               ("TEMP_AC_INY", "T° agua caliente impulsión", "°C", "temp"),
               ("SDP_INY", "Presión ducto inyección", "Pa", None), ("SDP_RET", "Presión ducto retorno", "Pa", None),
               ("MOD_VDF_INY", "Variador inyección", "%", None), ("MOD_VDF_RET", "Variador retorno", "%", None))


def fecha(ts, seg=False):
    return datetime.fromtimestamp(ts).strftime("%d-%m-%Y %H:%M" + (":%S" if seg else "")) if ts else "—"


def n(v, dec=1):
    if v is None:
        return "—"
    txt = f"{v:,.{max(dec, 0)}f}"
    return txt.replace(",", "§").replace(".", ",").replace("§", ".")


def es_de_sala(ambito, sala):
    nombre, uma = sala["nombre"], sala["uma"]
    return (ambito == nombre or (ambito or "").startswith(nombre + " ·")
            or (ambito or "").endswith(f"UMA {uma}"))


def contexto(mon, desde, hasta, sala_id=None):
    """Todo lo que necesitan informe y Excel, para todas las salas o para una."""
    inst = mon.instantanea()
    salas = inst["salas"]
    if sala_id:
        salas = [s for s in salas if s["id"] == sala_id]
        if not salas:
            raise ValueError(f"sala desconocida: {sala_id}")
    plaus = mon.lim.get("plausible", {})
    rangos, validos, claves = {}, {}, []
    for s in salas:
        for mag in ("temp", "hum", "pres"):
            m = s.get(mag)
            if not m or not m.get("clave"):
                continue
            claves.append(m["clave"])
            if mag in plaus:
                validos[m["clave"]] = tuple(plaus[mag])
            if mag in ("temp", "hum") and m.get("lim"):
                rangos[m["clave"]] = tuple(m["lim"])
            if mag == "pres" and m.get("min") is not None:
                rangos[m["clave"]] = (m["min"], plaus.get("pres", (0, 50))[1])
        u = inst["umas"].get(s["uma"])
        for senal, _, _, mag in SENALES_UMA:
            c = u["claves"].get(senal) if u else None
            if c:
                claves.append(c)
                if mag:
                    validos[c] = tuple(plaus[mag])
    stats = mon.estadisticas(desde, hasta, rangos, validos)
    eventos = mon.eventos(100000, desde, hasta)
    activas = inst["alarmas"]
    if sala_id:
        eventos = [e for e in eventos if es_de_sala(e["ambito"], salas[0])]
        activas = [a for a in activas if es_de_sala(a["ambito"], salas[0])]
    ctx = {"inst": inst, "salas": salas, "sala_id": sala_id, "desde": desde, "hasta": hasta,
           "stats": stats, "eventos": eventos, "activas": activas, "claves": claves,
           "titulo": salas[0]["nombre"] if sala_id else "Pabellones quirúrgicos y salas de parto"}
    ctx["observaciones"] = observaciones(mon, ctx)
    return ctx


def observaciones(mon, ctx):
    """[(nivel, texto)] redactadas a partir de lecturas, estadísticas y alarmas."""
    inst, st, obs = ctx["inst"], ctx["stats"], []
    if not ctx["sala_id"]:
        caidos = [c for c in inst["controladores"] if c["en_linea"] is False]
        en_linea = len(inst["controladores"]) - len(caidos)
        obs.append(("ok" if not caidos else "aviso",
                    f"{en_linea} de {len(inst['controladores'])} controladores responden por BACnet/IP." +
                    (" Sin respuesta: " + ", ".join(f"{c['id']} ({c['ip']})" for c in caidos) +
                     ". Revisar alimentación del controlador y enlace de red del tablero." if caidos else "")))
    for s in ctx["salas"]:
        u = inst["umas"].get(s["uma"]) or {}
        nom = s["nombre"]
        if u and not u.get("en_linea"):
            obs.append(("alarma", f"{nom}: sin comunicación con el controlador de la UMA {s['uma']}; "
                                  "los valores mostrados son los últimos guardados."))
        for mag, txt, uni in (("temp", "temperatura", "°C"), ("hum", "humedad relativa", "%")):
            m = s[mag]
            if m["estado"] == "sensor":
                obs.append(("aviso", f"{nom}: el sensor de {txt} entrega {n(m['valor'])} {uni}, un valor "
                                     "físicamente imposible. Revisar la sonda y su cableado."))
            e = st.get(m.get("clave"))
            if e and e.get("en_rango") is not None and e["en_rango"] < 0.95 and e["n"] >= 6:
                lo, hi = m["lim"]
                obs.append(("aviso", f"{nom}: {txt} dentro del rango {n(lo, 0)}–{n(hi, 0)} {uni} solo el "
                                     f"{n(e['en_rango'] * 100, 0)} % del período (mín. {n(e['min'])}, "
                                     f"máx. {n(e['max'])} {uni})."))
            if e and e.get("descartados"):
                obs.append(("info", f"{nom}: {e['descartados']} lecturas de {txt} fuera de rango físico "
                                    "se excluyeron de las estadísticas."))
        if s["hum"].get("sp") is not None and s["hum"]["sp"] < 30:
            obs.append(("info", f"{nom}: la consigna de humedad de la UMA {s['uma']} es {n(s['hum']['sp'], 0)} %, "
                                "muy inferior a la de las demás UMA (50–55 %). Verificar si es intencional."))
        p = s.get("pres")
        if p and p.get("estado") == "ok" and p.get("sp") and p["valor"] is not None and p["valor"] > p["sp"] * 5:
            obs.append(("info", f"{nom}: presión de sala {n(p['valor'])} Pa, muy por sobre la consigna "
                                f"({n(p['sp'])} Pa). Contrastar con un manómetro: podría ser la sonda."))
        if u:
            if u.get("mando") == "MANUAL OFF":
                obs.append(("aviso", f"{nom}: UMA {s['uma']} detenida en MANUAL OFF."))
            elif u.get("mando") == "MANUAL ON":
                obs.append(("info", f"{nom}: UMA {s['uma']} en MANUAL ON (no sigue el horario automático)."))
            for f, v in (u.get("filtros") or {}).items():
                if v == 1:
                    obs.append(("aviso", f"{nom}: filtro {f} de la UMA {s['uma']} con alarma de suciedad. "
                                         "Programar el cambio de filtro."))
            for falla in u.get("fallas") or []:
                obs.append(("alarma", f"{nom}: UMA {s['uma']} con {falla}."))
            for tramo, txt in (("aire", "aire"), ("agua_fria", "agua fría"), ("agua_caliente", "agua caliente")):
                for k, m in (u.get(tramo) or {}).items():
                    if isinstance(m, dict) and m.get("estado") == "sensor":
                        obs.append(("info", f"{nom}: sensor de {txt} ({k}) de la UMA {s['uma']} fuera de rango "
                                            f"({n(m['valor'])} °C)."))
    if not ctx["sala_id"]:
        ap = inst["apoyo"]
        gas = [f"{t} {g}" for t, gs in ap["gases"].items() for g, v in gs.items() if v == 1]
        obs.append(("alarma" if gas else "ok", "Gases clínicos: " + (
            "alarmas activas en " + ", ".join(gas) + "." if gas else
            f"sin alarmas en los {len(ap['gases'])} tableros TDG.")))
        bypass = [u for u, s in ap["ups"].items() if s.get("BYPASS") == 1]
        if bypass:
            obs.append(("info", f"{len(bypass)} de {len(ap['ups'])} UPS reportan bypass. La señal no indica por sí "
                                "sola pérdida de batería; confirmar el modo de operación en cada equipo."))
    nal = sum(1 for e in ctx["eventos"] if e["nivel"] == "alarma" and e["tipo"] == "ACTIVA")
    if nal:
        obs.append(("aviso", f"Se activaron {nal} alarma(s) durante el período (detalle en el registro de eventos)."))
    if not any(e.get("n") for e in st.values()):
        obs.append(("info", "Todavía no hay historial guardado en el período: las estadísticas y gráficos "
                            "se completan a medida que el dashboard registra lecturas (cada 5 minutos)."))
    orden = {"alarma": 0, "aviso": 1, "info": 2, "ok": 3}
    return sorted(obs, key=lambda o: orden[o[0]])
