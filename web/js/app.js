"use strict";
/* Dashboard Pabellones — solo lectura. Todo texto que viene del PLC se escapa con esc(). */

const $ = (id) => document.getElementById(id);
const ESTADO = { datos: null, sparks: {}, puntos: [], modal: null, periodo: { horas: 168 } };
const NIVEL = { alarma: 0, aviso: 1, info: 2, ok: 3 };
const COLORES = ["#4dc3ff", "#f9a825", "#2ecc8f", "#c08cff", "#ff8a5c", "#ff4d5e"];

function esc(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function num(v, d = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toLocaleString("es-CL", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function hora(ts, conFecha = false) {
  if (!ts) return "—";
  const f = new Date(ts * 1000);
  const h = f.toLocaleTimeString("es-CL", { hour: "2-digit", minute: "2-digit" });
  return conFecha ? f.toLocaleDateString("es-CL", { day: "2-digit", month: "2-digit" }) + " " + h : h;
}
function hace(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return `hace ${Math.round(s)} s`;
  if (s < 5400) return `hace ${Math.round(s / 60)} min`;
  if (s < 129600) return `hace ${Math.round(s / 3600)} h`;
  return `hace ${Math.round(s / 86400)} días`;
}
async function api(ruta) {
  const r = await fetch(ruta, { cache: "no-store" });
  if (!r.ok) throw new Error(`${ruta}: ${r.status}`);
  return r.json();
}

/* ---------------------------------------------------------------- estado */

function alarmasDe(nombre, uma) {
  const al = ESTADO.datos?.alarmas || [];
  return al.filter((a) => a.ambito === nombre || a.ambito.startsWith(nombre + " ·") ||
    (uma && (a.ambito === `UMA ${uma}` || a.ambito.endsWith(`UMA ${uma}`))));
}
function peor(lista) {
  return lista.reduce((p, a) => (NIVEL[a.nivel] < NIVEL[p] ? a.nivel : p), "ok");
}
function nivelMedida(m, lim) {
  if (!m || m.valor === null) return "sin";
  if (m.estado === "sensor") return "sensor";
  if (lim && (m.valor < lim[0] || m.valor > lim[1])) return "aviso";
  return "ok";
}

/* ---------------------------------------------------------------- render */

function render(d) {
  ESTADO.datos = d;
  $("demo").classList.toggle("oculto", !d.demo);
  // controladores en la barra
  $("ctrl-puntos").innerHTML = d.controladores.map((c) =>
    `<span class="${c.en_linea ? "on" : c.en_linea === false ? "off" : ""}" title="${esc(c.id)} · ${esc(c.ip)} · ${c.en_linea ? "en línea" : "sin respuesta"}"></span>`).join("");
  const pulso = $("pulso");
  const enLinea = d.controladores.filter((c) => c.en_linea).length;
  pulso.className = "pulso " + (enLinea ? "ok" : "caido");
  void pulso.offsetWidth; pulso.classList.add("late");
  pulso.title = `Último ciclo ${hora(d.ts)} · ${num(d.duracion_ciclo, 1)} s`;

  renderBanner(d);
  renderKpis(d);
  const pab = d.salas.filter((s) => s.tipo === "pabellon");
  $("pabellones").innerHTML = pab.map((s) => tarjetaSala(s, d)).join("");
  $("partos").innerHTML = d.salas.filter((s) => s.tipo !== "pabellon").map((s) => tarjetaSala(s, d)).join("");
  const enRango = pab.filter((s) => !alarmasDe(s.nombre, s.uma).some((a) => a.nivel !== "info")).length;
  $("nota-pab").textContent = `${enRango} de ${pab.length} sin alarmas ni avisos`;
  renderAlarmas(d.alarmas);
  renderGases(d.apoyo.gases);
  renderUps(d.apoyo.ups);
  renderUmas(d.umas);
  renderAmbiente(d.apoyo.ambiente);
  renderPrecision(d.apoyo.precision);
  renderOtros(d.apoyo);
  renderControladores(d);
  renderDescargas(d);
  dibujarSparks();
  if (ESTADO.modal) refrescarModal();
}

function renderBanner(d) {
  const al = d.alarmas.filter((a) => a.nivel === "alarma");
  const av = d.alarmas.filter((a) => a.nivel === "aviso");
  const b = $("banner");
  const caidos = d.controladores.filter((c) => c.en_linea === false);
  if (al.length) {
    b.className = "banner banner-alarma"; $("banner-icono").textContent = "▲";
    $("banner-texto").textContent = al.length === 1 ? al[0].mensaje : `${al.length} alarmas activas · ${al[0].mensaje}`;
  } else if (av.length) {
    b.className = "banner banner-aviso"; $("banner-icono").textContent = "●";
    $("banner-texto").textContent = `Sin alarmas · ${av.length} aviso${av.length > 1 ? "s" : ""} de mantenimiento u operación`;
  } else {
    b.className = "banner banner-ok"; $("banner-icono").textContent = "✓";
    $("banner-texto").textContent = "Pabellones y salas de parto dentro de los rangos configurados";
  }
  $("banner-det").textContent = `Lectura ${hora(d.ts)} · ${d.controladores.filter((c) => c.en_linea).length}/${d.controladores.length} controladores` +
    (caidos.length ? ` · sin respuesta: ${caidos.map((c) => c.id).join(", ")}` : "");
}

function kpi(et, v, det, nivel) {
  return `<div class="kpi ${nivel}"><div class="et">${esc(et)}</div><div class="v">${v}</div><div class="d">${esc(det)}</div></div>`;
}

function renderKpis(d) {
  const pab = d.salas.filter((s) => s.tipo === "pabellon");
  const temps = pab.map((s) => s.temp.estado === "ok" ? s.temp.valor : null).filter((v) => v !== null);
  const rango = pab.filter((s) => nivelMedida(s.temp, s.temp.lim) === "ok").length;
  const al = d.alarmas.filter((a) => a.nivel === "alarma").length;
  const av = d.alarmas.filter((a) => a.nivel === "aviso").length;
  const umas = Object.values(d.umas);
  const marcha = umas.filter((u) => u.estado === "marcha").length;
  const fallas = umas.filter((u) => u.estado === "falla").length;
  const gasAl = Object.values(d.apoyo.gases).flatMap((g) => Object.values(g)).filter((v) => v === 1).length;
  const ups = Object.values(d.apoyo.ups);
  const upsAl = ups.filter((u) => u.GENERAL === 1 || u.BATERIA === 1 || u.SOBRECARGA === 1).length;
  const bypass = ups.filter((u) => u.BYPASS === 1).length;
  const ctrl = d.controladores.filter((c) => c.en_linea).length;
  $("kpis").innerHTML = [
    kpi("Temperatura pabellones", `${rango}/${pab.length}`,
      temps.length ? `en rango · ${num(Math.min(...temps))}–${num(Math.max(...temps))} °C` : "sin lecturas", rango === pab.length ? "ok" : "aviso"),
    kpi("Alarmas activas", `${al}`, `${av} aviso${av === 1 ? "" : "s"}`, al ? "alarma" : av ? "aviso" : "ok"),
    kpi("UMA en marcha", `${marcha}/${umas.length}`, fallas ? `${fallas} con falla` : "sin fallas de ventilador", fallas ? "alarma" : "ok"),
    kpi("Gases clínicos", gasAl ? `${gasAl}` : "Normal", gasAl ? "señales de alarma activas" : `${Object.keys(d.apoyo.gases).length} tableros TDG sin alarma`, gasAl ? "alarma" : "ok"),
    kpi("UPS", upsAl ? `${upsAl} en alarma` : `${ups.length}`, `${bypass} reportan bypass`, upsAl ? "alarma" : bypass ? "aviso" : "ok"),
    kpi("Controladores", `${ctrl}/${d.controladores.length}`, `ciclo ${num(d.duracion_ciclo, 1)} s · cada ${d.intervalo_seg} s`, ctrl === d.controladores.length ? "ok" : "aviso"),
  ].join("");
}

function barraRango(m, lim) {
  if (!lim || m.valor === null || m.estado !== "ok") return "";
  const lo = lim[0] - 3, hi = lim[1] + 3;
  const pos = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  return `<div class="rango" title="Rango configurado ${num(lim[0])}–${num(lim[1])} °C">
      <span class="marca" style="left:${pos(m.valor)}%"></span>
      ${m.sp !== null && m.sp !== undefined ? `<span class="sp-marca" style="left:${pos(m.sp)}%" title="Consigna ${num(m.sp)} °C"></span>` : ""}
    </div>
    <div class="rango-et"><span>${num(lim[0], 0)} °C</span><span>rango configurado</span><span>${num(lim[1], 0)} °C</span></div>`;
}

function metrica(et, m, unidad, d, nivel, det) {
  const txt = m.estado === "sensor" ? "sensor" : num(m.valor, d);
  return `<div class="met ${nivel}"><div class="et">${esc(et)}</div>
    <div class="v">${txt}${m.estado === "ok" ? ` <small>${unidad}</small>` : ""}</div><div class="d">${det}</div></div>`;
}

function chipUma(u) {
  if (!u) return "";
  if (!u.en_linea) return `<span class="chip">UMA ${esc(u.id)} · sin comunicación</span>`;
  const n = { marcha: "ok", falla: "alarma", detenida: "aviso", sin_confirmar: "info" }[u.estado];
  const t = { marcha: "en marcha", falla: "falla", detenida: "detenida", sin_confirmar: "sin confirmar" }[u.estado];
  return `<span class="chip ${n}">UMA ${esc(u.id)} · ${t}</span>`;
}

function tarjetaSala(s, d) {
  const u = d.umas[s.uma];
  const als = alarmasDe(s.nombre, s.uma).filter((a) => a.nivel !== "info");
  let nivel = peor(als);
  const sinDato = s.temp.valor === null || (u && !u.en_linea);
  if (sinDato) nivel = "sin-dato";
  const estadoChip = sinDato ? `<span class="chip">sin datos actuales</span>`
    : nivel === "ok" ? `<span class="chip ok">Normal</span>`
    : `<span class="chip ${nivel}">${nivel === "alarma" ? "Alarma" : "Aviso"}</span>`;
  const hum = s.hum, pres = s.pres;
  const nh = nivelMedida(hum, hum.lim);
  const np = !pres ? "sin" : pres.estado === "sensor" ? "sensor" : pres.min !== null && pres.valor !== null && pres.valor < pres.min ? "aviso" : "ok";
  const iny = u?.inyeccion || {};
  const filtrosSucios = u ? Object.entries(u.filtros).filter(([, v]) => v === 1).map(([k]) => k) : [];
  const temperatura = s.temp.estado === "sensor" ? "sensor" : num(s.temp.valor);
  return `<article class="sala ${nivel}" data-sala="${esc(s.id)}" tabindex="0">
    <div class="sala-cab"><h3>${esc(s.nombre)}</h3>${estadoChip}</div>
    <div class="temp-grande"><span class="v">${temperatura}</span><span class="u">°C</span>
      <span class="sp">consigna<br><b>${num(s.temp.sp)} °C</b>${s.temp.banda !== null ? ` ±${num(s.temp.banda / 2)}` : ""}</span></div>
    ${barraRango(s.temp, s.temp.lim)}
    <div class="metricas">
      ${metrica("Humedad relativa", hum, "%", 0, nh, hum.sp !== null ? `consigna ${num(hum.sp, 0)} %` : "")}
      ${pres ? metrica("Presión de sala", pres, "Pa", 1, np, pres.sp !== null ? `consigna ${num(pres.sp)} Pa` : "") :
        `<div class="met"><div class="et">Ventilación</div><div class="v">${num(iny.vdf, 0)} <small>%</small></div><div class="d">variador de inyección</div></div>`}
    </div>
    <div class="sala-pie">
      ${chipUma(u)}
      ${u?.mando && u.mando !== "AUTOMÁTICO" ? `<span class="chip ${u.mando === "MANUAL OFF" ? "aviso" : "info"}">${esc(u.mando)}</span>` : ""}
      ${filtrosSucios.length ? `<span class="chip aviso">Filtro ${filtrosSucios.join(", ")}</span>` : ""}
      ${s.temp.viejo && s.temp.valor !== null ? `<span class="chip">dato ${hace(ESTADO.datos.ts_datos)}</span>` : ""}
    </div>
    <svg class="spark" data-clave="${esc(s.temp.clave || "")}" viewBox="0 0 300 34" preserveAspectRatio="none"></svg>
  </article>`;
}

function renderAlarmas(al) {
  const visibles = al.filter((a) => a.nivel !== "info");
  const info = al.filter((a) => a.nivel === "info");
  $("alarmas-n").textContent = `${visibles.length} activas · ${info.length} informativas`;
  if (!al.length) { $("alarmas").innerHTML = `<li class="vacio">Sin alarmas ni avisos</li>`; return; }
  $("alarmas").innerHTML = al.map((a) => `<li class="${a.nivel}"><span class="punto"></span>
      <span class="msg">${esc(a.mensaje)}</span><span class="cuando">${a.nivel.toUpperCase()} · desde ${hora(a.desde, true)} (${hace(a.desde)})</span></li>`).join("");
}

function renderGases(gases) {
  const tdg = Object.keys(gases).sort();
  const cols = ["O2", "AIRE", "VACIO", "N2"];
  const nombres = { O2: "O₂", AIRE: "Aire", VACIO: "Vacío", N2: "N₂" };
  const celda = (v) => v === undefined ? `<td class="na">—</td>` : v === 1 ? `<td class="alarma">ALARMA</td>` : v === 0 ? `<td class="ok">OK</td>` : `<td class="na">?</td>`;
  $("gases").innerHTML = `<thead><tr><th>Tablero</th>${cols.map((c) => `<th>${nombres[c]}</th>`).join("")}</tr></thead>
    <tbody>${tdg.map((t) => `<tr><td class="nombre">${esc(t)}</td>${cols.map((c) => celda(gases[t][c])).join("")}</tr>`).join("")}</tbody>`;
}

function renderUps(ups) {
  const nombres = Object.keys(ups).sort();
  const bypass = nombres.filter((n) => ups[n].BYPASS === 1).length;
  $("ups-nota").textContent = `${nombres.length} equipos · ${bypass} en bypass`;
  $("ups").innerHTML = nombres.map((n) => {
    const u = ups[n];
    const al = ["GENERAL", "BATERIA", "SOBRECARGA"].filter((k) => u[k] === 1);
    const chip = al.length ? `<span class="chip alarma">${al.join(" · ").toLowerCase()}</span>`
      : u.BYPASS === 1 ? `<span class="chip aviso">bypass</span>` : `<span class="chip ok">normal</span>`;
    return `<div class="u" title="${esc(n)}"><span>${esc(n.replace("UPS ", ""))}</span>${chip}</div>`;
  }).join("");
}

function barraMini(v) {
  if (v === null || v === undefined) return "—";
  return `<span class="barra-mini"><i style="width:${Math.max(0, Math.min(100, v))}%"></i></span>${num(v, 0)} %`;
}

function renderUmas(umas) {
  const filas = Object.values(umas).map((u) => {
    const n = { marcha: "ok", falla: "alarma", detenida: "aviso", sin_confirmar: "info" }[u.estado];
    const filtros = Object.entries(u.filtros).map(([k, v]) => v === 1 ? `<span class="chip aviso">${k}</span>` : "").join(" ") || `<span class="tenue">ok</span>`;
    return `<tr data-uma="${esc(u.id)}">
      <td><b>${esc(u.id)}</b></td><td>${esc(u.sala || "—")}</td>
      <td>${u.en_linea ? `<span class="chip ${n}">${esc(u.estado.replace("_", " "))}</span>` : `<span class="chip">sin comunicación</span>`}</td>
      <td>${esc(u.mando || "—")}</td>
      <td class="num">${medida(u.aire.inyeccion)}</td><td class="num">${medida(u.aire.retorno)}</td>
      <td>${barraMini(u.inyeccion.vdf)}</td><td class="num">${num(u.inyeccion.sdp, 0)} / ${num(u.inyeccion.sdp_sp, 0)} Pa</td>
      <td>${barraMini(u.agua_fria.valvula)}</td><td>${barraMini(u.agua_caliente.valvula)}</td>
      <td>${filtros}</td><td class="num">${num(u.inyeccion.horas, 0)} h</td></tr>`;
  });
  $("umas").innerHTML = `<thead><tr><th>UMA</th><th>Sirve a</th><th>Estado</th><th>Mando</th><th>T° inyección</th>
    <th>T° retorno</th><th>Variador iny.</th><th>Presión ducto iny. / consigna</th><th>Válv. frío</th><th>Válv. calor</th>
    <th>Filtros</th><th>Horas iny.</th></tr></thead><tbody>${filas.join("")}</tbody>`;
}

function renderAmbiente(amb) {
  const unidad = { co2: "ppm", hum: "%", pres: "Pa", temp: "°C" };
  const lim = ESTADO.datos.limites.co2_aviso;
  $("ambiente").innerHTML = amb.map((a) => {
    const cls = a.estado === "sensor" ? "tenue" : a.mag === "co2" && a.valor > lim ? "aviso" : "";
    const v = a.estado === "sensor" ? `sensor (${num(a.valor, 0)})` : `${num(a.valor, a.mag === "co2" ? 0 : 1)} ${unidad[a.mag]}`;
    return `<li><span>${esc(a.nombre)}</span><span class="${cls}">${v}</span></li>`;
  }).join("");
}

function renderPrecision(eps) {
  $("precision").innerHTML = Object.keys(eps).sort().map((k) => {
    const e = eps[k];
    const senales = Object.entries(e).filter(([s]) => s.startsWith("ALARMA")).map(([s, v]) =>
      `<span class="chip ${v === 1 ? "" : v === 0 ? "ok" : ""}" title="Valor ${v ?? "sin respuesta"}">${esc(s.replace("ALARMA_", "").replace("_", " ").toLowerCase())}: ${v ?? "—"}</span>`).join(" ");
    return `<div class="mini-tit">${esc(k)} · ${e.FUNCIONAMIENTO === 1 ? "en funcionamiento" : "detenido"}</div>
      <ul class="lista"><li><span>Temperatura</span><span>${num(e.TEMPERATURA)} °C</span></li>
      <li><span>Humedad</span><span>${num(e.HUMEDAD, 0)} %</span></li>
      <li><span>Horas de trabajo</span><span>${num(e.HORAS_DE_TRABAJO, 0)} h</span></li></ul>
      <div class="sala-pie" style="margin-top:6px">${senales}</div>`;
  }).join("") + `<p class="nota" style="margin-top:8px">Las señales de alarma de los EP valen 1 en los tres equipos a la vez: lógica por confirmar, no se tratan como alarma.</p>`;
}

function renderOtros(ap) {
  const scar = Object.keys(ap.scar).sort().map((k) => {
    const m = ap.scar[k].medida;
    return `<li><span>${esc(k)}</span><span>${m.estado === "ok" ? num(m.valor) + " °C" : "—"}</span></li>`;
  }).join("");
  const vec = ap.extractor["VEC PA.1"] || {};
  const eqL = Object.keys(ap.equipos_l).sort().map((k) => {
    const e = ap.equipos_l[k];
    const mal = e.AVERIA === 1 || e.FUERA_SERVICIO === 1;
    return `<li><span>Equipo ${esc(k)}</span><span class="${mal ? "aviso" : "ok"}">${mal ? "avería / fuera de servicio" : "sin avería"}</span></li>`;
  }).join("");
  $("otros").innerHTML = `<div class="mini-tit">SCAR 1–6 · temperatura</div><ul class="lista">${scar}</ul>
    <div class="mini-tit">Extractor VEC PA.1</div><ul class="lista">
      <li><span>Estado</span><span class="${vec.ESTADO === 1 ? "ok" : ""}">${vec.ESTADO === 1 ? "en marcha" : "detenido"}</span></li>
      <li><span>Térmico / presión</span><span class="${vec.TERMICO === 1 || vec["PRESIÓN"] === 1 ? "aviso" : "ok"}">${vec.TERMICO === 1 || vec["PRESIÓN"] === 1 ? "falla" : "normal"}</span></li>
      <li><span>Horas</span><span>${num(vec.HORAS_DE_FUNCIONAMIENTO, 0)} h</span></li></ul>
    <div class="mini-tit">Equipos L1 / L2 (por identificar)</div><ul class="lista">${eqL}</ul>
    <div class="mini-tit">Circuitos de alumbrado</div><ul class="lista">
      <li><span>Encendidos</span><span>${ap.circuitos.encendidos} de ${ap.circuitos.total}</span></li></ul>`;
}

function renderControladores(d) {
  $("ciclo-nota").textContent = `Último ciclo ${hora(d.ts)} · ${num(d.duracion_ciclo, 1)} s`;
  $("controladores").innerHTML = `<thead><tr><th>Controlador</th><th>Tablero</th><th>IP</th><th>Estado</th>
    <th>Puntos</th><th>Sin respuesta</th><th>Última lectura</th><th>Contenido</th></tr></thead><tbody>` +
    d.controladores.map((c) => `<tr><td><b>${esc(c.id)}</b></td><td>${esc(c.tablero)}</td><td>${esc(c.ip)}</td>
      <td>${c.en_linea ? `<span class="chip ok">en línea</span>` : `<span class="chip alarma">sin respuesta</span>`}</td>
      <td class="num">${c.puntos}</td><td class="num">${c.sin_respuesta ?? "—"}</td><td>${c.ultima_ok ? hace(c.ultima_ok) : "nunca"}</td>
      <td class="tenue">${esc(c.rol)}</td></tr>`).join("") + "</tbody>";
}

/* ------------------------------------------------------------ gráficos */

function linea(serie, w, h, min, max) {
  const t0 = serie[0][0], t1 = serie[serie.length - 1][0] || t0 + 1;
  const x = (t) => ((t - t0) / (t1 - t0 || 1)) * w;
  const y = (v) => h - ((v - min) / (max - min || 1)) * (h - 4) - 2;
  return serie.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
}

function dibujarSparks() {
  document.querySelectorAll("svg.spark").forEach((svg) => {
    const s = ESTADO.sparks[svg.dataset.clave];
    if (!s || s.length < 2) { svg.innerHTML = ""; return; }
    const vals = s.map((p) => p[1]);
    const min = Math.min(...vals) - 0.2, max = Math.max(...vals) + 0.2;
    const l = linea(s, 300, 34, min, max);
    svg.innerHTML = `<title>Últimas 24 h: ${num(Math.min(...vals))}–${num(Math.max(...vals))} °C</title>
      <path class="area" d="${l}L300,34L0,34Z"/><path class="linea" d="${l}"/>`;
  });
}

async function cargarSparks() {
  if (!ESTADO.datos) return;
  const claves = ESTADO.datos.salas.map((s) => s.temp.clave).filter(Boolean);
  try {
    const h = await api(`/api/historial?horas=24&claves=${encodeURIComponent(claves.join(","))}`);
    ESTADO.sparks = h.series;
    dibujarSparks();
  } catch (e) { console.warn(e); }
}

function grafico(series, unidad, ref) {
  const w = 1000, h = 230, m = { l: 48, r: 12, t: 12, b: 26 };
  // Un sensor dañado (p. ej. -60 °C) aplasta la escala: se ocultan valores no plausibles.
  const plaus = { "°C": [-10, 60], "%": [0, 100] }[unidad];
  let ocultos = 0;
  if (plaus) series = series.map((s) => ({ ...s, datos: s.datos.filter((p) => {
    const ok = p[1] >= plaus[0] && p[1] <= plaus[1]; if (!ok) ocultos++; return ok; }) }));
  const todas = series.flatMap((s) => s.datos);
  if (todas.length < 2) return (ocultos ? `<p class="nota aviso">Solo hay lecturas fuera de rango físico: sensor dañado.</p>` : "") + `<svg class="grafico" viewBox="0 0 ${w} ${h}"><text x="${w / 2}" y="${h / 2}" text-anchor="middle">Sin historial todavía · se guarda cada 5 minutos</text></svg>`;
  let min = Math.min(...todas.map((p) => p[2] ?? p[1])), max = Math.max(...todas.map((p) => p[3] ?? p[1]));
  if (ref) ref.forEach((r) => { if (r.v !== null && r.v !== undefined) { min = Math.min(min, r.v); max = Math.max(max, r.v); } });
  const pad = (max - min) * 0.08 || 1; min -= pad; max += pad;
  const t0 = Math.min(...todas.map((p) => p[0])), t1 = Math.max(...todas.map((p) => p[0]));
  const X = (t) => m.l + ((t - t0) / (t1 - t0 || 1)) * (w - m.l - m.r);
  const Y = (v) => h - m.b - ((v - min) / (max - min)) * (h - m.t - m.b);
  let svg = "";
  for (let i = 0; i <= 4; i++) {
    const v = min + ((max - min) * i) / 4, y = Y(v);
    svg += `<line class="rejilla" x1="${m.l}" x2="${w - m.r}" y1="${y}" y2="${y}"/><text x="${m.l - 6}" y="${y + 4}" text-anchor="end">${num(v, 1)}</text>`;
  }
  for (let i = 0; i <= 5; i++) {
    const t = t0 + ((t1 - t0) * i) / 5;
    svg += `<text x="${X(t)}" y="${h - 6}" text-anchor="middle">${hora(t, t1 - t0 > 86400)}</text>`;
  }
  (ref || []).forEach((r) => {
    if (r.v === null || r.v === undefined) return;
    svg += `<line x1="${m.l}" x2="${w - m.r}" y1="${Y(r.v)}" y2="${Y(r.v)}" stroke="${r.color}" stroke-dasharray="5 4" stroke-width="1.2" opacity=".8"/>`;
  });
  series.forEach((s, i) => {
    if (s.datos.length < 2) return;
    const d = s.datos.map((p, j) => `${j ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    svg += `<path d="${d}" fill="none" stroke="${s.color || COLORES[i]}" stroke-width="1.8"/>`;
  });
  const ley = series.map((s, i) => `<span><i style="background:${s.color || COLORES[i]}"></i>${esc(s.nombre)}</span>`).join("") +
    (ref || []).filter((r) => r.v !== null && r.v !== undefined).map((r) => `<span><i style="background:${r.color}"></i>${esc(r.nombre)}</span>`).join("");
  const nota = ocultos ? ` <span class="aviso">· ${ocultos} lecturas fuera de rango físico ocultas (sensor)</span>` : "";
  return `<svg class="grafico" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${svg}</svg><div class="leyenda">${ley} <span>· ${esc(unidad)}</span>${nota}</div>`;
}

/* --------------------------------------------------------------- modal */

function fila(et, v) { return `<div class="fila"><span>${esc(et)}</span><span>${v}</span></div>`; }
function bin(v, si, no) { return v === 1 ? si : v === 0 ? no : "—"; }
function medida(m, u = "°C") { return !m || m.valor === null ? "—" : m.estado === "sensor" ? `<span class="tenue">sensor (${num(m.valor)})</span>` : `${num(m.valor)} ${u}`; }

function detalleUma(u) {
  const f = Object.entries(u.filtros).map(([k, v]) => fila(`Filtro ${k}`, v === 1 ? `<span class="aviso">sucio</span>` : v === 0 ? `<span class="ok">ok</span>` : "—")).join("");
  return `<div class="det-grid">
    <div class="det-caja"><h4>Operación</h4>${fila("Estado", esc(u.estado.replace("_", " ")))}${fila("Mando general", esc(u.mando || "—"))}
      ${fila("Mando inyector / retorno", `${esc(u.mando_iny || "—")} / ${esc(u.mando_ret || "—")}`)}
      ${fila("Horario", bin(u.horario, "habilitado", "fuera de horario"))}${fila("Fallas", u.fallas.length ? `<span class="alarma">${esc(u.fallas.join(", "))}</span>` : `<span class="ok">ninguna</span>`)}</div>
    <div class="det-caja"><h4>Ventilador de inyección</h4>${fila("Marcha", bin(u.inyeccion.marcha, '<span class="ok">en marcha</span>', "detenido"))}
      ${fila("Orden", bin(u.inyeccion.orden, "ON", "OFF"))}${fila("Variador", u.inyeccion.vdf === null ? "—" : num(u.inyeccion.vdf, 0) + " %")}
      ${fila("Presión ducto", `${num(u.inyeccion.sdp, 0)} Pa`)}${fila("Consigna", `${num(u.inyeccion.sdp_sp, 0)} Pa`)}
      ${fila("Selector", bin(u.inyeccion.moa, "AUTO", "no automático"))}${fila("Horas", num(u.inyeccion.horas, 0) + " h")}</div>
    <div class="det-caja"><h4>Ventilador de retorno</h4>${fila("Marcha", bin(u.retorno.marcha, '<span class="ok">en marcha</span>', "detenido"))}
      ${fila("Orden", bin(u.retorno.orden, "ON", "OFF"))}${fila("Variador", u.retorno.vdf === null ? "—" : num(u.retorno.vdf, 0) + " %")}
      ${fila("Presión ducto", `${num(u.retorno.sdp, 0)} Pa`)}${fila("Consigna", `${num(u.retorno.sdp_sp, 0)} Pa`)}
      ${fila("Selector", bin(u.retorno.moa, "AUTO", "no automático"))}${fila("Horas", num(u.retorno.horas, 0) + " h")}</div>
    <div class="det-caja"><h4>Aire</h4>${fila("Mezcla", medida(u.aire.mezcla))}${fila("Inyección", medida(u.aire.inyeccion))}${fila("Retorno", medida(u.aire.retorno))}
      ${u.co2 !== null ? fila("CO₂", num(u.co2, 0) + " ppm") : ""}${u.temp_exterior !== null ? fila("Exterior", num(u.temp_exterior) + " °C") : ""}</div>
    <div class="det-caja"><h4>Agua fría</h4>${fila("Impulsión", medida(u.agua_fria.iny))}${fila("Retorno", medida(u.agua_fria.ret))}
      ${fila("Válvula", u.agua_fria.valvula === null ? "—" : num(u.agua_fria.valvula, 0) + " %")}${fila("Flujo serpentín", bin(u.agua_fria.flujo, '<span class="ok">sí</span>', '<span class="aviso">no</span>'))}</div>
    <div class="det-caja"><h4>Agua caliente</h4>${fila("Impulsión", medida(u.agua_caliente.iny))}${fila("Retorno", medida(u.agua_caliente.ret))}
      ${fila("Válvula", u.agua_caliente.valvula === null ? "—" : num(u.agua_caliente.valvula, 0) + " %")}${fila("Flujo serpentín", bin(u.agua_caliente.flujo, '<span class="ok">sí</span>', '<span class="aviso">no</span>'))}</div>
    <div class="det-caja"><h4>Humectador y filtros</h4>${fila("Humectador", bin(u.humectador.orden, "ON", "OFF"))}${fila("Horas humectador", num(u.humectador.horas, 0) + " h")}${f}</div>
  </div>`;
}

async function abrirModal(tipo, id) {
  ESTADO.modal = { tipo, id, horas: ESTADO.modal?.horas || 24 };
  $("modal").classList.remove("oculto");
  refrescarModal();
}

async function refrescarModal() {
  const d = ESTADO.datos, mo = ESTADO.modal;
  if (!d || !mo) return;
  let cab = "", cuerpo = "", graf = [];
  if (mo.tipo === "sala") {
    const s = d.salas.find((x) => x.id === mo.id); const u = d.umas[s.uma];
    const als = alarmasDe(s.nombre, s.uma);
    cab = `<h2>${esc(s.nombre)}</h2>${chipUma(u)}${s.nota ? `<span class="nota">${esc(s.nota)}</span>` : ""}
      <span class="acciones-barra" style="margin-left:auto;margin-right:34px">${botonesDoc(s.id)}</span>`;
    cuerpo = `<div class="det-grid" style="margin-bottom:12px">
      <div class="det-caja"><h4>Sala</h4>${fila("Temperatura", medida(s.temp))}${fila("Consigna", num(s.temp.sp) + " °C")}
        ${fila("Banda muerta", num(s.temp.banda) + " °C")}${fila("Humedad", medida(s.hum, "%"))}${fila("Consigna humedad", num(s.hum.sp, 0) + " %")}
        ${s.pres ? fila("Presión", medida(s.pres, "Pa")) + fila("Consigna presión", num(s.pres.sp) + " Pa") : ""}</div>
      <div class="det-caja" style="grid-column: span 2"><h4>Alarmas y avisos</h4>${als.length ? als.map((a) => `<div class="fila"><span class="${a.nivel}">${esc(a.mensaje)}</span><span>${hace(a.desde)}</span></div>`).join("") : `<span class="ok">Sin alarmas</span>`}</div>
    </div>` + (u ? detalleUma(u) : "");
    const lim = s.temp.lim || [];
    graf = [
      { titulo: "Temperatura de sala", unidad: "°C", series: [{ clave: s.temp.clave, nombre: "Temperatura" }],
        ref: [{ v: s.temp.sp, nombre: "Consigna", color: "#f9a825" }, { v: lim[0], nombre: "Mín.", color: "#4dc3ff" }, { v: lim[1], nombre: "Máx.", color: "#ff4d5e" }] },
      { titulo: "Humedad relativa", unidad: "%", series: [{ clave: s.hum.clave, nombre: "Humedad" }], ref: [{ v: s.hum.sp, nombre: "Consigna", color: "#f9a825" }] },
    ];
    if (s.pres) graf.push({ titulo: "Presión de sala", unidad: "Pa", series: [{ clave: s.pres.clave, nombre: "Presión" }], ref: [{ v: s.pres.sp, nombre: "Consigna", color: "#f9a825" }] });
    if (u) graf.push({ titulo: `UMA ${u.id} · temperaturas de aire`, unidad: "°C", series: [
      { clave: u.claves.TEMP_INY, nombre: "Inyección" }, { clave: u.claves.TEMP_RET, nombre: "Retorno" }] });
  } else {
    const u = d.umas[mo.id];
    cab = `<h2>UMA ${esc(u.id)}</h2>${chipUma(u)}<span class="nota">${esc(u.sala || "")} · ${esc(u.ip || "")}</span>`;
    cuerpo = detalleUma(u);
    graf = [
      { titulo: "Temperaturas de aire", unidad: "°C", series: [{ clave: u.claves.TEMP_INY, nombre: "Inyección" }, { clave: u.claves.TEMP_RET, nombre: "Retorno" }] },
      { titulo: "Presión de ducto", unidad: "Pa", series: [{ clave: u.claves.SDP_INY, nombre: "Inyección" }, { clave: u.claves.SDP_RET, nombre: "Retorno" }],
        ref: [{ v: u.inyeccion.sdp_sp, nombre: "Consigna iny.", color: "#f9a825" }] },
      { titulo: "Variadores", unidad: "%", series: [{ clave: u.claves.MOD_VDF_INY, nombre: "Inyección" }, { clave: u.claves.MOD_VDF_RET, nombre: "Retorno" }] },
    ];
  }
  const claves = graf.flatMap((g) => g.series.map((s) => s.clave)).filter(Boolean);
  let hist = { series: {} };
  try { hist = await api(`/api/historial?horas=${mo.horas}&claves=${encodeURIComponent(claves.join(","))}`); } catch (e) { console.warn(e); }
  if (ESTADO.modal !== mo) return;
  const sel = [24, 168, 720].map((hh) => `<button data-horas="${hh}" class="${mo.horas === hh ? "activo" : ""}">${hh === 24 ? "24 h" : hh === 168 ? "7 días" : "30 días"}</button>`).join("");
  $("modal-cuerpo").innerHTML = `<div class="det-cab">${cab}</div>${cuerpo}` + graf.map((g, i) => `
    <div class="graf-cab"><h2>${esc(g.titulo)}</h2>${i === 0 ? `<div class="selector">${sel}</div>` : ""}</div>
    ${grafico(g.series.filter((s) => s.clave).map((s, j) => ({ nombre: s.nombre, datos: hist.series[s.clave] || [], color: COLORES[j] })), g.unidad, g.ref)}`).join("");
}

function cerrarModal() { ESTADO.modal = null; $("modal").classList.add("oculto"); }

/* ------------------------------------------------- informes y descargas */

function botonesDoc(sala) {
  return `<button class="btn" type="button" data-doc="informe" data-sala="${esc(sala)}">📄 Informe</button>
    <button class="btn btn-sec" type="button" data-doc="excel" data-sala="${esc(sala)}">⬇ Excel</button>`;
}

function renderDescargas(d) {
  const cont = $("descargas");
  if (cont.dataset.listo) return;          // se arma una vez: no interrumpe un clic en curso
  cont.dataset.listo = "1";
  cont.innerHTML = `<div class="descarga general"><span class="nom">General<small>7 pabellones · salas de parto · UMA · gases · UPS</small></span>${botonesDoc("")}</div>` +
    d.salas.map((s) => `<div class="descarga"><span class="nom">${esc(s.nombre)}<small>UMA ${esc(s.uma)}</small></span>${botonesDoc(s.id)}</div>`).join("");
  textoPeriodo();
}

function consultaPeriodo() {
  const p = ESTADO.periodo;
  return p.horas ? `horas=${p.horas}` : `desde=${encodeURIComponent(p.desde)}&hasta=${encodeURIComponent(p.hasta)}`;
}

function textoPeriodo() {
  const p = ESTADO.periodo;
  $("periodo-txt").textContent = p.horas
    ? `Últimas ${p.horas === 24 ? "24 horas" : p.horas / 24 + " días"} hasta el momento de la descarga`
    : p.desde && p.hasta ? `Del ${p.desde.split("-").reverse().join("-")} al ${p.hasta.split("-").reverse().join("-")} (incluido)` : "Elija ambas fechas";
}

function elegirPeriodo(valor) {
  document.querySelectorAll("#sel-periodo button").forEach((b) => b.classList.toggle("activo", b.dataset.periodo === valor));
  const personal = valor === "personal";
  $("fechas").classList.toggle("oculto", !personal);
  if (personal) {
    if (!$("f-hasta").value) {
      const hoy = new Date(), ini = new Date(Date.now() - 7 * 86400000);
      const iso = (f) => `${f.getFullYear()}-${String(f.getMonth() + 1).padStart(2, "0")}-${String(f.getDate()).padStart(2, "0")}`;
      $("f-hasta").value = iso(hoy); $("f-desde").value = iso(ini);
    }
    ESTADO.periodo = { desde: $("f-desde").value, hasta: $("f-hasta").value };
  } else {
    ESTADO.periodo = { horas: +valor };
  }
  textoPeriodo();
}

async function descargar(boton) {
  const doc = boton.dataset.doc, sala = boton.dataset.sala || "";
  const p = ESTADO.periodo;
  if (!p.horas && (!p.desde || !p.hasta || p.desde > p.hasta)) {
    $("msg-descarga").textContent = "Revise las fechas del período."; return;
  }
  const url = `/api/${doc}?${consultaPeriodo()}${sala ? `&sala=${encodeURIComponent(sala)}` : ""}`;
  const nombre = sala ? ESTADO.datos.salas.find((s) => s.id === sala)?.nombre : "general";
  if (doc === "informe") {
    window.open(url, "_blank", "noopener");
    $("msg-descarga").textContent = `Informe ${nombre} abierto en una pestaña nueva.`;
    return;
  }
  boton.disabled = true;
  const texto = boton.textContent;
  boton.textContent = "Generando…";
  $("msg-descarga").textContent = `Generando Excel ${nombre}…`;
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `error ${r.status}`);
    const blob = await r.blob();
    const archivo = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || "pabellones.xlsx";
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = archivo;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    $("msg-descarga").textContent = `Descargado ${archivo}`;
  } catch (e) {
    $("msg-descarga").textContent = `No se pudo generar el Excel: ${e.message}`;
  } finally {
    boton.disabled = false; boton.textContent = texto;
  }
}

/* ------------------------------------------------------ eventos / puntos */

async function cargarEventos() {
  try {
    const r = await api("/api/eventos?limite=100");
    $("eventos").innerHTML = r.eventos.length ? r.eventos.map((e) => `<li class="${esc(e.nivel)}"><span class="t">${hora(e.ts, true)}</span><span class="m">${esc(e.mensaje)}</span></li>`).join("")
      : `<li class="vacio">Sin eventos registrados</li>`;
  } catch (e) { console.warn(e); }
}

async function cargarPuntos() {
  try { ESTADO.puntos = (await api("/api/puntos")).puntos; filtrarPuntos(); } catch (e) { console.warn(e); }
}

function valorPunto(p) {
  if (p.valor === null || p.valor === undefined) return `<span class="tenue">sin respuesta</span>`;
  if (p.estados) return esc(p.estados[p.valor - 1] ?? p.valor);
  if (p.tipo.startsWith("binary")) return p.valor === 1 ? `<b>1</b>${p.activo ? ` <span class="tenue">${esc(p.activo)}</span>` : ""}` : "0";
  return `${num(p.valor, 2)} ${esc(p.unidad || "")}`;
}

function filtrarPuntos() {
  const q = $("buscar").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const lista = ESTADO.puntos.filter((p) => {
    const txt = `${p.nombre} ${p.ip} ${p.uma || ""} ${p.senal || ""} ${p.grupo}`.toLowerCase();
    return q.every((t) => txt.includes(t));
  });
  $("pts-n").textContent = `${lista.length} de ${ESTADO.puntos.length} puntos`;
  const tipo = { analogInput: "AI", analogOutput: "AO", analogValue: "AV", binaryInput: "BI", binaryOutput: "BO", binaryValue: "BV", multiStateValue: "MV" };
  $("puntos").innerHTML = `<thead><tr><th>Señal</th><th>Controlador</th><th>Objeto</th><th>Valor</th></tr></thead><tbody>` +
    lista.slice(0, 300).map((p) => `<tr><td>${esc(p.nombre)}</td><td>${esc(p.ip.split(".").pop())}</td><td>${tipo[p.tipo]} ${p.inst}</td><td>${valorPunto(p)}</td></tr>`).join("") +
    (lista.length > 300 ? `<tr><td colspan="4" class="tenue">… ${lista.length - 300} más: afine la búsqueda</td></tr>` : "") + "</tbody>";
}

/* ---------------------------------------------------------------- inicio */

async function actualizar() {
  try { render(await api("/api/estado")); }
  catch (e) {
    $("banner").className = "banner banner-alarma"; $("banner-icono").textContent = "▲";
    $("banner-texto").textContent = "Sin conexión con el servicio del dashboard (¿se cerró la ventana de consola?)";
    $("pulso").className = "pulso caido";
  }
}

document.addEventListener("click", (ev) => {
  const doc = ev.target.closest("[data-doc]"); if (doc) return descargar(doc);
  const per = ev.target.closest("[data-periodo]"); if (per) return elegirPeriodo(per.dataset.periodo);
  const sala = ev.target.closest("[data-sala]"); if (sala) return abrirModal("sala", sala.dataset.sala);
  const uma = ev.target.closest("[data-uma]"); if (uma) return abrirModal("uma", uma.dataset.uma);
  const h = ev.target.closest("[data-horas]"); if (h && ESTADO.modal) { ESTADO.modal = { ...ESTADO.modal, horas: +h.dataset.horas }; return refrescarModal(); }
  if (ev.target.id === "modal" || ev.target.id === "modal-cerrar") cerrarModal();
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") cerrarModal();
  if (ev.key === "Enter" && ev.target.dataset?.sala) abrirModal("sala", ev.target.dataset.sala);
});
$("buscar").addEventListener("input", filtrarPuntos);
["f-desde", "f-hasta"].forEach((id) => $(id).addEventListener("change", () => elegirPeriodo("personal")));
setInterval(() => { $("reloj").textContent = new Date().toLocaleString("es-CL", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }); }, 1000);

(async () => {
  await actualizar();
  cargarSparks(); cargarEventos(); cargarPuntos();
  setInterval(actualizar, 10000);
  setInterval(cargarEventos, 30000);
  setInterval(cargarPuntos, 60000);
  setInterval(cargarSparks, 300000);
})();
