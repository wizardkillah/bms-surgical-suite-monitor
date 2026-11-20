"""Informe técnico en HTML listo para imprimir o guardar como PDF (A4), general o por sala.

Sin dependencias: el navegador del PC lo imprime o lo guarda en PDF.
"""
import html
import os
from datetime import datetime

from . import DIR_WEB, __version__
from .reportes import SENALES_UMA, contexto, es_de_sala, fecha, n

LOGO_SVG = os.path.join(DIR_WEB, "img", "logo_hospital.svg")
E = html.escape
COLORES = ["#1f6fd1", "#d1491f", "#1a9c63", "#8a4fd8", "#e0a100", "#0f8fa8", "#c2185b", "#5d6b7d"]
MANDO_TXT = {"AUTOMÁTICO": "Automático", "MANUAL ON": "Manual ON", "MANUAL OFF": "Manual OFF"}
ESTADO_UMA = {"marcha": "En marcha", "falla": "Falla", "detenida": "Detenida", "sin_confirmar": "Sin confirmar"}


def _logo():
    try:
        with open(LOGO_SVG, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "<b>Hospital Demo</b>"


def _grafico(series, desde, hasta, unidad, refs=(), valido=None, ancho=700, alto=190):
    """SVG de líneas. series: {nombre: [[t, prom, min, max], …]}; refs: [(valor, nombre, color)]."""
    if valido:
        series = {k: [p for p in s if valido[0] <= p[1] <= valido[1]] for k, s in series.items()}
    vals = [p[1] for s in series.values() for p in s] + [r[0] for r in refs if r[0] is not None]
    if not any(series.values()):
        return '<p class="nota">Sin historial en el período.</p>'
    # Con poco historial el eje se ajusta a los datos disponibles (si no, la curva queda pegada al borde).
    primero = min(p[0] for s_ in series.values() for p in s_)
    recorte = primero > desde + (hasta - desde) * 0.1
    if recorte:
        desde = primero
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    ml, mr, mt, mb = 46, 10, 8, 22
    X = lambda t: ml + (t - desde) / max(hasta - desde, 1) * (ancho - ml - mr)  # noqa: E731
    Y = lambda v: alto - mb - (v - lo) / (hi - lo) * (alto - mt - mb)       # noqa: E731
    s = [f'<svg viewBox="0 0 {ancho} {alto}" class="graf" font-family="Segoe UI, Arial" font-size="9.5">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        s.append(f'<line x1="{ml}" x2="{ancho - mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e1e6ee"/>'
                 f'<text x="{ml - 5}" y="{Y(v) + 3:.1f}" text-anchor="end" fill="#667">{n(v, 1)}</text>')
    fmt = "%d-%m" if hasta - desde > 3 * 86400 else "%H:%M"
    for i in range(6):
        t = desde + (hasta - desde) * i / 5
        s.append(f'<text x="{X(t):.1f}" y="{alto - 6}" text-anchor="middle" fill="#667">'
                 f'{datetime.fromtimestamp(t).strftime(fmt)}</text>')
    for v, _, c in refs:
        if v is not None:
            s.append(f'<line x1="{ml}" x2="{ancho - mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{c}" '
                     'stroke-dasharray="5 4" stroke-width="1"/>')
    for k, (nombre, serie) in enumerate(series.items()):
        if not serie:
            continue
        paso = min((b[0] - a[0] for a, b in zip(serie, serie[1:])), default=3600)
        d, prev = [], None
        for t, v, *_ in serie:
            d.append(f"{'M' if prev is None or t - prev > max(paso * 3, 1800) else 'L'}{X(t):.1f},{Y(v):.1f}")
            prev = t
        s.append(f'<path d="{" ".join(d)}" fill="none" stroke="{COLORES[k % len(COLORES)]}" stroke-width="1.5"/>')
    s.append("</svg>")
    ley = "".join(f'<span><i style="background:{COLORES[k % len(COLORES)]}"></i>{E(nm)}</span>'
                  for k, nm in enumerate(series))
    ley += "".join(f'<span><i style="background:{c}"></i>{E(nm)}</span>' for v, nm, c in refs if v is not None)
    if recorte:
        ley += f"<span>· historial disponible desde {datetime.fromtimestamp(primero):%d-%m-%Y %H:%M}</span>"
    return "".join(s) + f'<div class="ley">{ley}<span>· {E(unidad)}</span></div>'


def _estado_sala(ctx, s):
    als = [a for a in ctx["inst"]["alarmas"] if es_de_sala(a["ambito"], s) and a["nivel"] != "info"]
    if any(a["nivel"] == "alarma" for a in als):
        return "alarma", "Alarma"
    return ("aviso", "Aviso") if als else ("ok", "Normal")


def _med(m, uni, dec=1):
    if not m or m.get("valor") is None:
        return "—"
    return "sensor" if m.get("estado") == "sensor" else f"{n(m['valor'], dec)} {uni}"


def _fila_stat(nombre, e, uni, dec=1, rango=True):
    if not e or not e.get("n"):
        return f"<tr><td>{E(nombre)}</td><td colspan='5' class='nota'>sin historial en el período</td></tr>"
    en = n(e["en_rango"] * 100, 0) + " %" if rango and e.get("en_rango") is not None else "—"
    celdas = "".join(f"<td class='n'>{n(e[k], dec)} {E(uni)}</td>" for k in ("min", "prom", "max"))
    return f"<tr><td>{E(nombre)}</td>{celdas}<td class='n'>{en}</td><td class='n'>{e['n']}</td></tr>"


def _tabla_stats(filas):
    return ("<table><tr><th>Variable</th><th>Mínimo</th><th>Promedio</th><th>Máximo</th>"
            "<th>En rango</th><th>Muestras</th></tr>" + "".join(filas) + "</table>")


def _detalle_uma(u):
    if not u:
        return '<p class="nota">Sin UMA asociada.</p>'
    def b(v, si, no):
        return si if v == 1 else no if v == 0 else "—"
    iny, ret = u["inyeccion"], u["retorno"]
    filtros = ", ".join(f"{k}: {'SUCIO' if v == 1 else 'ok' if v == 0 else '—'}" for k, v in u["filtros"].items())
    return f"""<table class="ficha">
<tr><td>Estado / mando general</td><td>{ESTADO_UMA.get(u['estado'], u['estado'])} · {E(MANDO_TXT.get(u['mando'], u['mando'] or '—'))}
  · horario {b(u['horario'], 'habilitado', 'fuera de horario')}</td></tr>
<tr><td>Ventilador de inyección</td><td>{b(iny['marcha'], 'en marcha', 'detenido')} · variador {n(iny['vdf'], 0)} % ·
  ducto {n(iny['sdp'], 0)} Pa (consigna {n(iny['sdp_sp'], 0)} Pa) · {n(iny['horas'], 0)} h</td></tr>
<tr><td>Ventilador de retorno</td><td>{b(ret['marcha'], 'en marcha', 'detenido')} · variador {n(ret['vdf'], 0)} % ·
  ducto {n(ret['sdp'], 0)} Pa (consigna {n(ret['sdp_sp'], 0)} Pa) · {n(ret['horas'], 0)} h</td></tr>
<tr><td>Aire (mezcla / inyección / retorno)</td><td>{_med(u['aire']['mezcla'], '°C')} · {_med(u['aire']['inyeccion'], '°C')}
  · {_med(u['aire']['retorno'], '°C')}</td></tr>
<tr><td>Agua fría (impulsión / retorno)</td><td>{_med(u['agua_fria']['iny'], '°C')} · {_med(u['agua_fria']['ret'], '°C')} ·
  válvula {n(u['agua_fria']['valvula'], 0)} % · flujo {b(u['agua_fria']['flujo'], 'sí', 'NO')}</td></tr>
<tr><td>Agua caliente (impulsión / retorno)</td><td>{_med(u['agua_caliente']['iny'], '°C')} · {_med(u['agua_caliente']['ret'], '°C')} ·
  válvula {n(u['agua_caliente']['valvula'], 0)} % · flujo {b(u['agua_caliente']['flujo'], 'sí', 'NO')}</td></tr>
<tr><td>Humectador</td><td>{b(u['humectador']['orden'], 'ON', 'OFF')} · {n(u['humectador']['horas'], 0)} h</td></tr>
<tr><td>Filtros</td><td>{E(filtros) or '—'}</td></tr>
<tr><td>Fallas</td><td>{E(', '.join(u['fallas'])) or 'ninguna'}</td></tr>
</table>"""


def _seccion_sala(mon, ctx, s, num, salto):
    inst, st, d, h = ctx["inst"], ctx["stats"], ctx["desde"], ctx["hasta"]
    u = inst["umas"].get(s["uma"])
    cls, txt = _estado_sala(ctx, s)
    t, hu, p = s["temp"], s["hum"], s.get("pres")
    lim_t, lim_h = t.get("lim") or [None, None], hu.get("lim") or [None, None]
    filas = [_fila_stat("Temperatura de sala", st.get(t.get("clave")), "°C"),
             _fila_stat("Humedad relativa", st.get(hu.get("clave")), "%", 0)]
    if p:
        filas.append(_fila_stat("Presión de sala", st.get(p.get("clave")), "Pa"))
    for senal, nombre, uni, _ in SENALES_UMA:
        c = u["claves"].get(senal) if u else None
        if c:
            filas.append(_fila_stat(f"UMA · {nombre}", st.get(c), uni, 0 if uni in ("Pa", "%") else 1, rango=False))
    claves = [x for x in (t.get("clave"), hu.get("clave"), p and p.get("clave")) if x]
    if u:
        claves += [u["claves"].get(k) for k in ("TEMP_INY", "TEMP_RET") if u["claves"].get(k)]
    hist = mon.historial(d, h, claves, max_puntos=300)["series"]
    plaus = mon.lim.get("plausible", {})
    graf = _grafico({"Temperatura": hist.get(t.get("clave"), [])}, d, h, "°C",
                    [(t.get("sp"), "Consigna", "#f9a825"), (lim_t[0], "Mínimo", "#4d9fff"), (lim_t[1], "Máximo", "#d32f2f")],
                    plaus.get("temp"))
    graf += _grafico({"Humedad": hist.get(hu.get("clave"), [])}, d, h, "%", [(hu.get("sp"), "Consigna", "#f9a825")],
                     plaus.get("hum"), alto=150)
    if p:
        graf += _grafico({"Presión": hist.get(p.get("clave"), [])}, d, h, "Pa", [(p.get("sp"), "Consigna", "#f9a825")],
                         plaus.get("pres"), alto=150)
    if u:
        graf += _grafico({"Aire inyección": hist.get(u["claves"].get("TEMP_INY"), []),
                          "Aire retorno": hist.get(u["claves"].get("TEMP_RET"), [])}, d, h, "°C", (),
                         plaus.get("temp"), alto=150)
    return f"""
<section class="{'salto' if salto else ''}">
<h2>{num}. {E(s['nombre'])} <span class="estado {cls}">{txt}</span></h2>
{f'<p class="nota">{E(s["nota"])}</p>' if s.get('nota') else ''}
<div class="kpis">
  <div class="kpi"><span>Temperatura</span><b>{_med(t, '°C')}</b><span>consigna {n(t.get('sp'))} °C · rango {n(lim_t[0], 0)}–{n(lim_t[1], 0)} °C</span></div>
  <div class="kpi"><span>Humedad relativa</span><b>{_med(hu, '%', 0)}</b><span>consigna {n(hu.get('sp'), 0)} % · rango {n(lim_h[0], 0)}–{n(lim_h[1], 0)} %</span></div>
  <div class="kpi"><span>{'Presión de sala' if p else 'Ventilación'}</span><b>{_med(p, 'Pa') if p else (n(u['inyeccion']['vdf'], 0) + ' %' if u else '—')}</b>
    <span>{'consigna ' + n(p.get('sp')) + ' Pa' if p else 'variador de inyección'}</span></div>
  <div class="kpi"><span>UMA {E(s['uma'])}</span><b>{ESTADO_UMA.get(u['estado'], '—') if u else '—'}</b>
    <span>{E(MANDO_TXT.get(u['mando'], u['mando'] or '—')) if u else ''}</span></div>
</div>
<h3>Estadísticas del período</h3>{_tabla_stats(filas)}
<p class="nota">«En rango» = porcentaje de las muestras dentro del rango configurado. Las lecturas físicamente imposibles (sensor dañado) se excluyen.</p>
<h3>Tendencias</h3>{graf}
<h3>Unidad de manejo de aire {E(s['uma'])}</h3>{_detalle_uma(u)}
</section>"""


def generar(mon, desde, hasta, sala_id=None):
    ctx = contexto(mon, desde, hasta, sala_id)
    inst, st = ctx["inst"], ctx["stats"]
    general = not sala_id
    folio = datetime.now().strftime(f"IT-PAB-{sala_id or 'GEN'}-%Y%m%d-%H%M")
    ctrl_ok = sum(1 for c in inst["controladores"] if c["en_linea"])
    obs = "".join(f'<li class="{c}">{E(t)}</li>' for c, t in ctx["observaciones"])
    n_al = sum(1 for a in ctx["activas"] if a["nivel"] == "alarma")
    n_av = sum(1 for a in ctx["activas"] if a["nivel"] == "aviso")

    partes, num = [], 3
    if general:
        filas = []
        for s in inst["salas"]:
            cls, txt = _estado_sala(ctx, s)
            u = inst["umas"].get(s["uma"]) or {}
            et = st.get(s["temp"].get("clave")) or {}
            filas.append(
                f"<tr class='{cls}'><td>{E(s['nombre'])}</td><td>{E(s['uma'])}</td><td class='n'>{_med(s['temp'], '°C')}</td>"
                f"<td class='n'>{n(s['temp'].get('sp'))}</td><td class='n'>{_med(s['hum'], '%', 0)}</td>"
                f"<td class='n'>{_med(s.get('pres'), 'Pa') if s.get('pres') else '—'}</td>"
                f"<td class='n'>{n(et.get('min'))} / {n(et.get('prom'))} / {n(et.get('max'))}</td>"
                f"<td class='n'>{n(et['en_rango'] * 100, 0) + ' %' if et.get('en_rango') is not None else '—'}</td>"
                f"<td>{ESTADO_UMA.get(u.get('estado'), '—')}</td><td>{txt}</td></tr>")
        claves = [s["temp"]["clave"] for s in inst["salas"] if s["tipo"] == "pabellon" and s["temp"].get("clave")]
        hist = mon.historial(desde, hasta, claves, max_puntos=300)["series"]
        nombres = {s["temp"]["clave"]: s["nombre"] for s in inst["salas"]}
        lim_pab = mon.lim.get("pabellon", {}).get("temp") or [None, None]
        graf = _grafico({nombres[c]: hist.get(c, []) for c in claves}, desde, hasta, "°C",
                        [(lim_pab[0], "Mínimo", "#4d9fff"), (lim_pab[1], "Máximo", "#d32f2f")],
                        mon.lim.get("plausible", {}).get("temp"), alto=230)
        ap = inst["apoyo"]
        gases = "".join(
            f"<tr><td>{E(t)}</td>" + "".join(
                f"<td class='{'alarma' if g.get(k) == 1 else ''}'>{'ALARMA' if g.get(k) == 1 else 'OK' if g.get(k) == 0 else '—'}</td>"
                for k in ("O2", "AIRE", "VACIO", "N2")) + "</tr>" for t, g in sorted(ap["gases"].items()))
        ups = "".join(
            f"<tr><td>{E(u)}</td><td>{', '.join(k.lower() for k in ('GENERAL', 'BATERIA', 'SOBRECARGA') if s.get(k) == 1) or 'sin alarma'}</td>"
            f"<td>{'sí' if s.get('BYPASS') == 1 else 'no'}</td></tr>" for u, s in sorted(ap["ups"].items()))
        umas = "".join(
            f"<tr><td>{E(u['id'])}</td><td>{E(u.get('sala') or '—')}</td><td>{ESTADO_UMA.get(u['estado'], '—') if u['en_linea'] else 'sin comunicación'}</td>"
            f"<td>{E(MANDO_TXT.get(u['mando'], u['mando'] or '—'))}</td><td class='n'>{_med(u['aire']['inyeccion'], '°C')}</td>"
            f"<td class='n'>{n(u['inyeccion']['vdf'], 0)} %</td><td class='n'>{n(u['inyeccion']['sdp'], 0)} / {n(u['inyeccion']['sdp_sp'], 0)}</td>"
            f"<td>{', '.join(k for k, v in u['filtros'].items() if v == 1) or 'ok'}</td></tr>" for u in inst["umas"].values())
        partes.append(f"""
<h2>3. Estado de las salas</h2>
<table><tr><th>Sala</th><th>UMA</th><th>T° actual</th><th>Consigna</th><th>HR</th><th>Presión</th>
<th>T° mín / prom / máx</th><th>T° en rango</th><th>UMA</th><th>Estado</th></tr>{''.join(filas)}</table>
<h3>Temperatura de los pabellones en el período</h3>{graf}
<h2>4. Unidades de manejo de aire</h2>
<table><tr><th>UMA</th><th>Sirve a</th><th>Estado</th><th>Mando</th><th>T° inyección</th><th>Variador</th>
<th>Ducto iny. / consigna (Pa)</th><th>Filtros sucios</th></tr>{umas}</table>
<h2>5. Sistemas de apoyo</h2>
<div class="dos"><div><h3>Gases clínicos</h3><table><tr><th>Tablero</th><th>O₂</th><th>Aire</th><th>Vacío</th><th>N₂</th></tr>{gases}</table></div>
<div><h3>UPS</h3><table><tr><th>Equipo</th><th>Alarmas</th><th>Bypass</th></tr>{ups}</table></div></div>""")
        num = 6
        for i, s in enumerate(inst["salas"]):
            partes.append(_seccion_sala(mon, ctx, s, f"{num}.{i + 1}", salto=(i == 0)))
        num += 1
    else:
        partes.append(_seccion_sala(mon, ctx, ctx["salas"][0], 3, salto=False))
        num = 4

    ev = ctx["eventos"][:300]
    eventos = "".join(f"<tr class='{e['nivel']}'><td>{fecha(e['ts'])}</td><td>{E(e['nivel'].upper())}</td>"
                      f"<td>{E(e['tipo'])}</td><td>{E(e['mensaje'])}</td></tr>" for e in ev) or \
        "<tr><td colspan='4' class='nota'>Sin eventos registrados en el período.</td></tr>"
    if len(ctx["eventos"]) > 300:
        eventos += f"<tr><td colspan='4' class='nota'>… {len(ctx['eventos']) - 300} eventos más en el Excel.</td></tr>"

    titulo = ("Informe técnico · Pabellones quirúrgicos y salas de parto" if general
              else f"Informe técnico · {ctx['salas'][0]['nombre']}")
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>{E(titulo)} — {datetime.fromtimestamp(desde):%d-%m-%Y} a {datetime.fromtimestamp(hasta):%d-%m-%Y}</title>
<style>
:root {{ --logo-text:#5b6068; --logo-accent:#f9a825; }}
@page {{ size: A4; margin: 13mm 12mm 15mm; }}
* {{ box-sizing: border-box; }}
body {{ font: 10pt/1.4 "Segoe UI", Arial, sans-serif; color:#1d2633; margin:0; background:#eef1f5; }}
.hoja {{ max-width: 210mm; margin: 0 auto; background:#fff; padding: 13mm 12mm; }}
.acciones {{ position: sticky; top:0; background:#1f3b5c; color:#fff; padding:8px 14px; display:flex; gap:10px; align-items:center; justify-content:center; z-index:2; }}
.acciones button {{ background:#f9a825; border:0; border-radius:6px; padding:6px 14px; font-weight:700; cursor:pointer; }}
header {{ display:grid; grid-template-columns: 185px 1fr; align-items:center; gap:16px; border-bottom:3px solid #f9a825; padding-bottom:10px; }}
header .logo svg {{ width:100%; height:auto; display:block; }}
header .tit {{ min-width:0; border-left:1px solid #dde2ea; padding-left:14px; }}
header h1 {{ margin:0; font-size:14.5pt; line-height:1.2; color:#1f3b5c; }}
header .sub {{ color:#5b6575; font-size:9pt; }} .folio {{ font-size:8.5pt; color:#8a94a6; margin-top:2px; }}
h2 {{ font-size:12pt; color:#1f3b5c; border-left:4px solid #f9a825; padding-left:8px; margin:18px 0 8px; page-break-after: avoid; }}
h3 {{ font-size:10pt; color:#38455a; margin:12px 0 4px; page-break-after: avoid; }}
table {{ width:100%; border-collapse:collapse; font-size:8.6pt; margin-bottom:6px; }}
th {{ background:#1f3b5c; color:#fff; text-align:left; padding:4px 6px; font-weight:600; }}
td {{ padding:3px 6px; border-bottom:1px solid #dde2ea; }} td.n {{ text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }}
td.alarma, tr.alarma td {{ color:#b00020; font-weight:600; }} tr.aviso td {{ color:#8a5a00; }}
tr {{ page-break-inside: avoid; }}
.ficha td:first-child {{ font-weight:600; width: 30%; color:#38455a; }}
.kpis {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:6px; }}
.kpi {{ border:1px solid #dde2ea; border-top:3px solid #f9a825; border-radius:6px; padding:6px 8px; }}
.kpi b {{ display:block; font-size:14pt; }} .kpi span {{ color:#5b6575; font-size:8pt; }}
.estado {{ font-size:8.5pt; border-radius:10px; padding:1px 8px; margin-left:6px; vertical-align:middle; }}
.estado.ok {{ background:#e3f4ea; color:#17683f; }} .estado.aviso {{ background:#fff1cf; color:#8a5a00; }} .estado.alarma {{ background:#fde2e2; color:#b00020; }}
ul.obs {{ margin:0; padding:0; list-style:none; }}
ul.obs li {{ padding:5px 8px 5px 10px; margin-bottom:4px; border-left:4px solid #8a94a6; background:#f6f8fb; page-break-inside: avoid; }}
ul.obs li.alarma {{ border-color:#d32f2f; background:#fdecec; }} ul.obs li.aviso {{ border-color:#f0a400; background:#fff7e0; }}
ul.obs li.ok {{ border-color:#1a9c63; background:#eaf7f0; }} ul.obs li.info {{ border-color:#1f6fd1; background:#ebf3fd; }}
.graf {{ width:100%; height:auto; }}
.ley {{ font-size:8pt; color:#5b6575; display:flex; flex-wrap:wrap; gap:12px; margin:0 0 8px 46px; }}
.ley i {{ display:inline-block; width:14px; height:3px; margin-right:4px; vertical-align:middle; }}
.nota {{ color:#5b6575; font-size:8pt; }}
.dos {{ display:grid; grid-template-columns: 1fr 1fr; gap:14px; }}
.firmas {{ display:grid; grid-template-columns: 1fr 1fr; gap:40px; margin-top:40px; page-break-inside: avoid; }}
.firmas div {{ border-top:1px solid #38455a; padding-top:4px; text-align:center; font-size:9pt; color:#38455a; }}
.salto {{ page-break-before: always; }}
footer {{ margin-top:16px; border-top:1px solid #dde2ea; padding-top:6px; font-size:8pt; color:#8a94a6; display:flex; justify-content:space-between; }}
@media print {{ body {{ background:#fff; }} .acciones {{ display:none; }} .hoja {{ padding:0; max-width:none; }}
  th, .kpi, ul.obs li, header, .estado {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }} }}
</style></head><body>
<div class="acciones"><span>Informe técnico generado.</span>
  <button id="imprimir" type="button">🖨 Imprimir / Guardar PDF</button></div>
<div class="hoja">
<header>
  <div class="logo">{_logo()}</div>
  <div class="tit"><h1>{E(titulo)}</h1>
    <div class="sub">Unidad de Mantenimiento · Piso 2, Edificio A · Sistema BMS Sauter (BACnet/IP)</div>
    <div class="folio">Folio {folio} · Emitido {datetime.now():%d-%m-%Y %H:%M}</div></div>
</header>

<h2>1. Identificación</h2>
<table class="ficha">
<tr><td>Alcance</td><td>{E('7 pabellones quirúrgicos, 4 salas integrales de parto, 17 UMA, gases clínicos y UPS' if general
     else ctx['salas'][0]['nombre'] + ' · UMA ' + ctx['salas'][0]['uma'])}</td></tr>
<tr><td>Período analizado</td><td>{fecha(desde)} a {fecha(hasta)}</td></tr>
<tr><td>Comunicación</td><td>{ctrl_ok} de {len(inst['controladores'])} controladores en línea · última lectura {fecha(inst['ts'])}</td></tr>
<tr><td>Alarmas / avisos activos</td><td>{n_al} alarma(s) · {n_av} aviso(s)</td></tr>
</table>

<h2>2. Observaciones</h2>
<ul class="obs">{obs}</ul>
{''.join(partes)}

<h2 class="salto">{num}. Registro de eventos del período</h2>
<table><tr><th>Fecha</th><th>Nivel</th><th>Tipo</th><th>Mensaje</th></tr>{eventos}</table>

<p class="nota">Los rangos de temperatura, humedad y presión son los configurados en el dashboard (config/pabellones.json) y no
constituyen una certificación normativa. Las observaciones se generan a partir de las lecturas y deben verificarse en terreno.</p>

<div class="firmas"><div>Revisado por (nombre y firma)</div><div>Jefatura de Mantenimiento</div></div>
<footer><span>Dashboard Pabellones v{__version__} · Hospital Demo</span><span>{folio}</span></footer>
</div>
<script src="/js/informe.js"></script>
</body></html>"""
