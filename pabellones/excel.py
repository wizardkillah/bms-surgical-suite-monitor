"""Excel de descarga, general o por sala: resumen, observaciones, historial con gráfico,
estadísticas, UMA, gases/UPS, eventos, puntos y una planilla de rondas para registro manual."""
import io
import os
from datetime import datetime

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import DIR_WEB, __version__
from .reportes import SENALES_UMA, contexto, es_de_sala

LOGO_PNG = os.path.join(DIR_WEB, "img", "logo_hospital.png")
AZUL = "1F3B5C"
ROJO = PatternFill("solid", fgColor="F8CBCB")
AMARILLO = PatternFill("solid", fgColor="FFE9A8")
VERDE = PatternFill("solid", fgColor="D5F0DF")
AZULITO = PatternFill("solid", fgColor="DCE9F8")
GRIS = PatternFill("solid", fgColor="EEF1F5")
CABECERA = PatternFill("solid", fgColor=AZUL)
BLANCO = Font(color="FFFFFF", bold=True)
FINO = Side(style="thin", color="BFC7D5")
BORDE = Border(left=FINO, right=FINO, top=FINO, bottom=FINO)
RELLENO = {"alarma": ROJO, "aviso": AMARILLO, "ok": VERDE, "info": AZULITO}
ESTADO_UMA = {"marcha": "En marcha", "falla": "Falla", "detenida": "Detenida", "sin_confirmar": "Sin confirmar"}
TIPO = {"analogInput": "AI", "analogOutput": "AO", "analogValue": "AV", "binaryInput": "BI",
        "binaryOutput": "BO", "binaryValue": "BV", "multiStateValue": "MV"}


def paso_para(desde, hasta):
    dias = (hasta - desde) / 86400
    return 300 if dias <= 2 else 900 if dias <= 10 else 3600 if dias <= 45 else 3 * 3600


def _txt_paso(p):
    return f"{p // 3600} h" if p >= 3600 else f"{p // 60} min"


def _f(ts):
    return datetime.fromtimestamp(ts) if ts else None


def _logo(ws, celda="A1"):
    try:
        from openpyxl.drawing.image import Image
        img = Image(LOGO_PNG)
        escala = 50 / img.height
        img.width, img.height = int(img.width * escala), 50
        ws.add_image(img, celda)
        return True
    except Exception:
        return False


def _cab(ws, fila, textos, col=1):
    for i, t in enumerate(textos, col):
        c = ws.cell(row=fila, column=i, value=t)
        c.fill, c.font, c.border = CABECERA, BLANCO, BORDE
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[fila].height = 30


def _fila(ws, fila, valores, fmt=None, relleno=None):
    for i, v in enumerate(valores, 1):
        c = ws.cell(row=fila, column=i, value=v)
        c.border = BORDE
        if fmt and i - 1 < len(fmt) and fmt[i - 1]:
            c.number_format = fmt[i - 1]
        if relleno:
            c.fill = relleno


def _anchos(ws, anchos):
    for i, a in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(i)].width = a


def _encabezado(ws, titulo, ctx, fila_titulo=4):
    _logo(ws)
    ws.cell(row=1, column=4, value="Hospital Demo · Unidad de Mantenimiento").font = Font(bold=True, color=AZUL, size=12)
    ws.cell(row=2, column=4, value=f"Período {_f(ctx['desde']):%d-%m-%Y %H:%M} a {_f(ctx['hasta']):%d-%m-%Y %H:%M} · "
                                    f"emitido {datetime.now():%d-%m-%Y %H:%M}").font = Font(color="5B6575")
    ws.cell(row=fila_titulo, column=1, value=titulo).font = Font(bold=True, size=14, color=AZUL)
    return fila_titulo + 2


def _r(v, d=1):
    return None if v is None else round(v, d)


def _med(m, d=1):
    if not m or m.get("valor") is None or m.get("estado") == "sensor":
        return None
    return round(m["valor"], d)


def _estado_sala(ctx, s):
    als = [a for a in ctx["inst"]["alarmas"] if es_de_sala(a["ambito"], s) and a["nivel"] != "info"]
    if any(a["nivel"] == "alarma" for a in als):
        return "alarma", "Alarma"
    return ("aviso", "Aviso") if als else ("ok", "Normal")


# ------------------------------------------------------------------- hojas

def _hoja_resumen(ws, ctx):
    inst, st = ctx["inst"], ctx["stats"]
    f = _encabezado(ws, f"Resumen · {ctx['titulo']}", ctx)
    cab = ["Sala", "UMA", "T° actual (°C)", "Consigna T° (°C)", "Rango T° (°C)", "HR actual (%)", "Consigna HR (%)",
           "Presión (Pa)", "Consigna presión (Pa)", "T° mín (°C)", "T° prom (°C)", "T° máx (°C)", "T° en rango",
           "HR en rango", "Estado UMA", "Mando", "Filtros sucios", "Estado"]
    _cab(ws, f, cab)
    for s in ctx["salas"]:
        f += 1
        u = inst["umas"].get(s["uma"]) or {}
        et = st.get(s["temp"].get("clave")) or {}
        eh = st.get(s["hum"].get("clave")) or {}
        p = s.get("pres") or {}
        lim = s["temp"].get("lim") or [None, None]
        nivel, txt = _estado_sala(ctx, s)
        _fila(ws, f, [s["nombre"], s["uma"], _med(s["temp"]), _r(s["temp"].get("sp")), f"{lim[0]:g}–{lim[1]:g}" if lim[0] is not None else None,
                      _med(s["hum"], 0), _r(s["hum"].get("sp"), 0), _med(p), _r(p.get("sp")),
                      _r(et.get("min")), _r(et.get("prom")), _r(et.get("max")), et.get("en_rango"), eh.get("en_rango"),
                      ESTADO_UMA.get(u.get("estado"), "—") if u.get("en_linea") else "Sin comunicación",
                      u.get("mando"), ", ".join(k for k, v in (u.get("filtros") or {}).items() if v == 1) or "—", txt],
              ["", "", "0.0", "0.0", "", "0", "0", "0.0", "0.0", "0.0", "0.0", "0.0", "0%", "0%"])
        ws.cell(row=f, column=18).fill = RELLENO[nivel]
        for col, m in ((3, s["temp"]), (6, s["hum"])):
            if m.get("estado") == "sensor":
                ws.cell(row=f, column=col, value="sensor").fill = GRIS
    f += 2
    ws.cell(row=f, column=1, value="Controladores").font = Font(bold=True, color=AZUL)
    f += 1
    _cab(ws, f, ["Controlador", "Tablero", "IP", "Estado", "Puntos", "Última lectura", "Contenido"])
    for c in inst["controladores"]:
        f += 1
        _fila(ws, f, [c["id"], c["tablero"], c["ip"], "En línea" if c["en_linea"] else "Sin respuesta",
                      c["puntos"], _f(c["ultima_ok"]), c["rol"]], ["", "", "", "", "0", "dd-mm-yyyy hh:mm"],
              None if c["en_linea"] else AMARILLO)
    _anchos(ws, [30, 8, 11, 11, 11, 11, 11, 11, 11, 10, 10, 10, 10, 10, 14, 13, 13, 10])
    ws.freeze_panes = "C7"


def _hoja_observaciones(ws, ctx):
    f = _encabezado(ws, "Observaciones automáticas", ctx)
    _cab(ws, f, ["Nivel", "Observación"])
    for nivel, texto in ctx["observaciones"]:
        f += 1
        _fila(ws, f, [nivel.upper(), texto])
        ws.cell(row=f, column=1).fill = RELLENO.get(nivel, GRIS)
        ws.cell(row=f, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    f += 2
    ws.cell(row=f, column=2, value="Generadas a partir de las lecturas y de los rangos configurados; deben verificarse "
                                   "en terreno. No constituyen una certificación normativa.").font = Font(italic=True, color="5B6575")
    _anchos(ws, [10, 120])


def _columnas_historial(ctx):
    cols = []
    for s in ctx["salas"]:
        pref = s["nombre"] if not ctx["sala_id"] else ""
        for mag, txt, uni in (("temp", "T°", "°C"), ("hum", "HR", "%"), ("pres", "Presión", "Pa")):
            m = s.get(mag)
            if m and m.get("clave"):
                cols.append((m["clave"], f"{pref} {txt} ({uni})".strip(), mag))
        if ctx["sala_id"]:
            u = ctx["inst"]["umas"].get(s["uma"])
            for senal, nombre, uni, mag in SENALES_UMA:
                c = u["claves"].get(senal) if u else None
                if c:
                    cols.append((c, f"UMA {s['uma']} · {nombre} ({uni})", mag))
    return cols


def _hoja_historial(wb, mon, ctx, paso):
    ws = wb.create_sheet("Historial")
    cols = _columnas_historial(ctx)
    tabla = mon.tabla(ctx["desde"], ctx["hasta"], paso, [c for c, _, _ in cols])
    plaus = mon.lim.get("plausible", {})
    ws.cell(row=1, column=1, value=f"Historial promediado cada {_txt_paso(paso)} · lecturas físicamente imposibles en blanco").font = Font(bold=True, color=AZUL)
    _cab(ws, 2, ["Fecha"] + [t for _, t, _ in cols])
    fila = 2
    for b in sorted(tabla):
        fila += 1
        vals = []
        for c, _, mag in cols:
            v = tabla[b].get(c)
            lo, hi = plaus.get(mag, (-1e9, 1e9)) if mag else (-1e9, 1e9)
            vals.append(round(v, 2) if v is not None and lo <= v <= hi else None)
        _fila(ws, fila, [_f(b)] + vals, ["dd-mm-yyyy hh:mm"] + ["0.0"] * len(cols))
    if fila == 2:
        ws.cell(row=3, column=1, value="Sin historial en el período (se guarda cada 5 minutos mientras el dashboard está abierto).")
    _anchos(ws, [17] + [16] * len(cols))
    ws.freeze_panes = "B3"
    temps = [i for i, (_, _, mag) in enumerate(cols, 2) if mag == "temp" and (ctx["sala_id"] is None or i < 3)]
    if fila > 3 and temps:
        g = wb.create_sheet("Gráfico T°")
        ch = LineChart()
        ch.title = f"Temperatura · {ctx['titulo']}"
        ch.y_axis.title, ch.x_axis.title = "°C", "Fecha"
        ch.height, ch.width = 12, 28
        ch.x_axis.number_format = "dd-mm hh:mm"
        for i in temps:
            ch.add_data(Reference(ws, min_col=i, min_row=2, max_row=fila), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=3, max_row=fila))
        g.add_chart(ch, "A1")


def _hoja_estadisticas(ws, mon, ctx):
    f = _encabezado(ws, "Estadísticas del período", ctx)
    _cab(ws, f, ["Variable", "Unidad", "Mínimo", "Promedio", "Máximo", "En rango", "Muestras", "Descartadas (sensor)",
                 "Primera", "Última"])
    for c, titulo, _ in _columnas_historial(ctx):
        e = ctx["stats"].get(c) or {}
        uni = mon.por_clave[c].get("unidad") or ""
        f += 1
        _fila(ws, f, [titulo, uni, _r(e.get("min"), 2), _r(e.get("prom"), 2), _r(e.get("max"), 2), e.get("en_rango"),
                      e.get("n"), e.get("descartados"), _f(e.get("primero")), _f(e.get("ultimo"))],
              ["", "", "0.0", "0.0", "0.0", "0%", "0", "0", "dd-mm-yyyy hh:mm", "dd-mm-yyyy hh:mm"])
    _anchos(ws, [44, 8, 10, 10, 10, 10, 10, 12, 17, 17])


def _hoja_umas(ws, ctx):
    f = _encabezado(ws, "Unidades de manejo de aire (lectura actual)", ctx)
    umas = [ctx["inst"]["umas"][s["uma"]] for s in ctx["salas"] if s["uma"] in ctx["inst"]["umas"]] \
        if ctx["sala_id"] else list(ctx["inst"]["umas"].values())
    cab = ["UMA", "Sirve a", "Estado", "Mando", "Horario", "T° mezcla", "T° inyección", "T° retorno",
           "Variador iny. (%)", "Ducto iny. (Pa)", "Consigna ducto iny. (Pa)", "Variador ret. (%)", "Ducto ret. (Pa)",
           "AF impulsión (°C)", "AF retorno (°C)", "Válv. frío (%)", "Flujo frío", "AC impulsión (°C)", "AC retorno (°C)",
           "Válv. calor (%)", "Flujo calor", "Humectador", "Filtros sucios", "Fallas", "Horas iny.", "Horas ret."]
    _cab(ws, f, cab)
    si = lambda v: "Sí" if v == 1 else "No" if v == 0 else None  # noqa: E731
    for u in umas:
        f += 1
        _fila(ws, f, [u["id"], u.get("sala"), ESTADO_UMA.get(u["estado"]) if u["en_linea"] else "Sin comunicación",
                      u["mando"], "Habilitado" if u["horario"] == 1 else "Fuera de horario" if u["horario"] == 0 else None,
                      _med(u["aire"]["mezcla"]), _med(u["aire"]["inyeccion"]), _med(u["aire"]["retorno"]),
                      u["inyeccion"]["vdf"], u["inyeccion"]["sdp"], u["inyeccion"]["sdp_sp"], u["retorno"]["vdf"],
                      u["retorno"]["sdp"], _med(u["agua_fria"]["iny"]), _med(u["agua_fria"]["ret"]),
                      u["agua_fria"]["valvula"], si(u["agua_fria"]["flujo"]), _med(u["agua_caliente"]["iny"]),
                      _med(u["agua_caliente"]["ret"]), u["agua_caliente"]["valvula"], si(u["agua_caliente"]["flujo"]),
                      "ON" if u["humectador"]["orden"] == 1 else "OFF" if u["humectador"]["orden"] == 0 else None,
                      ", ".join(k for k, v in u["filtros"].items() if v == 1) or "—", ", ".join(u["fallas"]) or "—",
                      u["inyeccion"]["horas"], u["retorno"]["horas"]],
              ["", "", "", "", "", "0.0", "0.0", "0.0", "0", "0", "0", "0", "0", "0.0", "0.0", "0", "", "0.0", "0.0", "0"])
        ws.cell(row=f, column=3).fill = RELLENO.get({"marcha": "ok", "falla": "alarma", "detenida": "aviso"}.get(u["estado"], "info"))
    _anchos(ws, [8, 30, 14, 13, 14] + [11] * 21)
    ws.freeze_panes = "C7"


def _hoja_apoyo(ws, ctx):
    ap = ctx["inst"]["apoyo"]
    f = _encabezado(ws, "Gases clínicos, UPS y ambiente (lectura actual)", ctx)
    _cab(ws, f, ["Tablero", "O₂", "Aire", "Vacío", "N₂"])
    for t, g in sorted(ap["gases"].items()):
        f += 1
        vals = ["ALARMA" if g.get(k) == 1 else "OK" if g.get(k) == 0 else "—" for k in ("O2", "AIRE", "VACIO", "N2")]
        _fila(ws, f, [t] + vals)
        for i, v in enumerate(vals, 2):
            ws.cell(row=f, column=i).fill = ROJO if v == "ALARMA" else VERDE if v == "OK" else GRIS
    f += 2
    _cab(ws, f, ["UPS", "General", "Batería", "Auto", "Sobrecarga", "Bypass"])
    for u, s in sorted(ap["ups"].items()):
        f += 1
        _fila(ws, f, [u] + ["Sí" if s.get(k) == 1 else "No" if s.get(k) == 0 else "—"
                            for k in ("GENERAL", "BATERIA", "AUTO", "SOBRECARGA", "BYPASS")])
    f += 2
    _cab(ws, f, ["Sonda de ambiente", "Valor", "Unidad", "Estado"])
    for a in ap["ambiente"]:
        f += 1
        _fila(ws, f, [a["nombre"], a["valor"], {"co2": "ppm", "hum": "%", "pres": "Pa", "temp": "°C"}[a["mag"]],
                      "Sensor fuera de rango" if a["estado"] == "sensor" else "OK"])
    f += 2
    _cab(ws, f, ["Equipo de precisión", "Temperatura (°C)", "Humedad (%)", "Horas", "Funcionamiento"])
    for k, e in sorted(ap["precision"].items()):
        f += 1
        _fila(ws, f, [k, e.get("TEMPERATURA"), e.get("HUMEDAD"), e.get("HORAS_DE_TRABAJO"), e.get("FUNCIONAMIENTO")])
    _anchos(ws, [24, 14, 14, 12, 14, 12])


def _hoja_eventos(ws, ctx):
    f = _encabezado(ws, "Registro de eventos del período", ctx)
    _cab(ws, f, ["Fecha", "Nivel", "Tipo", "Ámbito", "Mensaje", "Valor"])
    for e in ctx["eventos"]:
        f += 1
        _fila(ws, f, [_f(e["ts"]), e["nivel"].upper(), e["tipo"], e["ambito"], e["mensaje"], e["valor"]],
              ["dd-mm-yyyy hh:mm:ss"])
        ws.cell(row=f, column=2).fill = RELLENO.get(e["nivel"], GRIS)
    if not ctx["eventos"]:
        ws.cell(row=f + 1, column=1, value="Sin eventos en el período.")
    _anchos(ws, [19, 9, 14, 30, 90, 10])
    ws.freeze_panes = f"A{7}"


def _hoja_puntos(ws, mon, ctx):
    ws.cell(row=1, column=1, value="Todos los puntos BACnet leídos (valor actual)").font = Font(bold=True, color=AZUL)
    _cab(ws, 2, ["Controlador", "Objeto", "Nombre", "Grupo", "UMA", "Señal", "Valor", "Unidad / texto", "Leído"])
    ips = {s["uma"] for s in ctx["salas"]} if ctx["sala_id"] else None
    f = 2
    for p in mon.tabla_puntos():
        if ips is not None and p.get("uma") not in ips:
            continue
        f += 1
        v = p["valor"]
        txt = p.get("unidad") or ""
        if p.get("estados") and v is not None and 0 < v <= len(p["estados"]):
            txt = p["estados"][int(v) - 1]
        elif p["tipo"].startswith("binary") and v == 1:
            txt = p.get("activo") or "activo"
        _fila(ws, f, [p["ip"], f"{TIPO[p['tipo']]} {p['inst']}", p["nombre"], p["grupo"], p.get("uma"), p.get("senal"),
                      v, txt, _f(p["ts"])], ["", "", "", "", "", "", "0.###", "", "dd-mm-yyyy hh:mm"])
    ws.auto_filter.ref = f"A2:I{f}"
    _anchos(ws, [14, 9, 42, 10, 7, 18, 11, 16, 17])
    ws.freeze_panes = "A3"


def _hoja_rondas(ws, ctx):
    """Planilla para registrar a mano las rondas de verificación con instrumento propio."""
    _logo(ws)
    ws.cell(row=1, column=4, value="Registro de rondas · verificación de condiciones ambientales").font = Font(bold=True, size=13, color=AZUL)
    ws.cell(row=2, column=4, value="Completar en terreno con instrumento calibrado. Columna «BMS» = valor del dashboard al momento de la medición.").font = Font(color="5B6575")
    cab = ["Fecha", "Hora", "Sala", "T° medida (°C)", "T° BMS (°C)", "Diferencia T° (°C)", "HR medida (%)", "HR BMS (%)",
           "Presión medida (Pa)", "Presión BMS (Pa)", "Puertas cerradas", "Instrumento / N° serie", "Responsable", "Observaciones"]
    _cab(ws, 5, cab)
    salas = [s["nombre"] for s in ctx["salas"]] if ctx["sala_id"] else [s["nombre"] for s in ctx["inst"]["salas"]]
    lista = DataValidation(type="list", formula1='"' + ",".join(salas) + '"', allow_blank=True)
    sino = DataValidation(type="list", formula1='"Sí,No"', allow_blank=True)
    ws.add_data_validation(lista)
    ws.add_data_validation(sino)
    for f in range(6, 66):
        for c in range(1, len(cab) + 1):
            celda = ws.cell(row=f, column=c)
            celda.border = BORDE
            if f % 2:
                celda.fill = GRIS
        ws.cell(row=f, column=1).number_format = "dd-mm-yyyy"
        ws.cell(row=f, column=2).number_format = "hh:mm"
        ws.cell(row=f, column=6, value=f'=IF(AND(ISNUMBER(D{f}),ISNUMBER(E{f})),D{f}-E{f},"")').number_format = "0.0"
        if ctx["sala_id"]:
            ws.cell(row=f, column=3, value=salas[0])
    lista.add("C6:C65")
    sino.add("K6:K65")
    _anchos(ws, [12, 8, 32, 12, 11, 12, 11, 10, 12, 12, 10, 20, 20, 40])
    ws.freeze_panes = "A6"


def generar(mon, desde, hasta, sala_id=None, paso=None):
    """Devuelve (bytes, nombre de archivo)."""
    ctx = contexto(mon, desde, hasta, sala_id)
    paso = paso or paso_para(desde, hasta)
    wb = Workbook()
    _hoja_resumen(wb.active, ctx)
    wb.active.title = "Resumen"
    _hoja_observaciones(wb.create_sheet("Observaciones"), ctx)
    _hoja_historial(wb, mon, ctx, paso)
    _hoja_estadisticas(wb.create_sheet("Estadísticas"), mon, ctx)
    _hoja_umas(wb.create_sheet("UMA"), ctx)
    if not sala_id:
        _hoja_apoyo(wb.create_sheet("Gases y UPS"), ctx)
    _hoja_eventos(wb.create_sheet("Eventos"), ctx)
    _hoja_puntos(wb.create_sheet("Puntos BACnet"), mon, ctx)
    _hoja_rondas(wb.create_sheet("Registro de rondas"), ctx)
    wb.properties.creator = f"Dashboard Pabellones v{__version__}"
    wb.properties.title = f"Pabellones · {ctx['titulo']}"
    buf = io.BytesIO()
    wb.save(buf)
    nombre = (f"pabellones_{(sala_id or 'general').lower()}_"
              f"{datetime.fromtimestamp(desde):%Y%m%d}-{datetime.fromtimestamp(hasta):%Y%m%d}.xlsx")
    return buf.getvalue(), nombre
