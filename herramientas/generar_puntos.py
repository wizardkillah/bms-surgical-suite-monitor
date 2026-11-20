"""Genera config/puntos.json a partir de un escaneo BACnet completo (descubrir.py).

    python herramientas/generar_puntos.py [herramientas/escaneo_demo.json]

Clasifica cada objeto por nombre: UMA (B1.x) + señal, sala, gases, UPS, equipos de
precisión, ambiente, circuitos… Los objetos internos (FILE, PROG, NC, SCHED, tipo 384),
los resets de alarma y las entradas de reserva (BI_xx) no se sondean.
Si una señal de una UMA aparece en dos controladores, se queda la del controlador
principal de esa UMA (orden de PREFERENCIA).
"""
import json
import os
import re
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIPOS = {"AI": "analogInput", "AO": "analogOutput", "AV": "analogValue", "BI": "binaryInput",
         "BO": "binaryOutput", "BV": "binaryValue", "MV": "multiStateValue"}
UNIDADES = {29: "%HR", 53: "Pa", 62: "°C", 71: "h", 96: "ppm", 98: "%"}
# Controlador preferido cuando una UMA aparece repetida (los 525.1 tienen la lógica).
PREFERENCIA = ["192.0.2.22", "192.0.2.24", "192.0.2.26",
               "192.0.2.25", "192.0.2.23", "192.0.2.21", "192.0.2.28"]
# Nombres con la UMA equivocada en el PLC (la descripción del objeto dice la correcta).
CORRECCIONES = {("192.0.2.22", "AV", 3): "B1.7_TEMPERATURA_PABELLON_7",
                ("192.0.2.22", "MV", 234): "B1.16_MANDO_RETORNO_B1.16",
                ("192.0.2.22", "AV", 188): "B1.15_SP_HUM_BANDA_B1.15"}

SENALES = [  # (regex sobre el nombre sin B1.x, señal)
    (r"^SDP_INY$", "SDP_INY"), (r"^SDP_RET$", "SDP_RET"),
    (r"^TEMP_MEZCLA$", "TEMP_MEZCLA"), (r"^TEMP_INY$", "TEMP_INY"), (r"^TEMP_RET$", "TEMP_RET"),
    (r"^TEMP_AF_INY$", "TEMP_AF_INY"), (r"^TEMP_AF_RET$", "TEMP_AF_RET"),
    (r"^TEMP_AC_INY$", "TEMP_AC_INY"), (r"^TEMP_AC_RET$", "TEMP_AC_RET"),
    (r"^MOD_VDF_INY$", "MOD_VDF_INY"), (r"^MOD_VDF_RET$", "MOD_VDF_RET"),
    (r"^MOD_VAL_FRIO$", "MOD_VAL_FRIO"), (r"^MOD_V(AL)?_CALOR$", "MOD_VAL_CALOR"),
    (r"^MOD_DAM_RET$", "MOD_DAM_RET"), (r"^MOD_DAM_INY$", "MOD_DAM_INY"), (r"^MOD_DAM_MEZ$", "MOD_DAM_MEZ"),
    (r"^EST_MOA_INY$", "EST_MOA_INY"), (r"^EST_MOA_RET$", "EST_MOA_RET"),
    (r"^EST_VDF_INY$", "EST_VDF_INY"), (r"^EST_VDF_RET$", "EST_VDF_RET"),
    (r"^(EST_RES_INY|ALARMA_VDF_INY?)$", "FALLA_VDF_INY"),
    (r"^(EST_RES_RET|ALARMA_VDF_RET)$", "FALLA_VDF_RET"),
    (r"FI?LTRO(_SUCIO)?_G4$", "FILTRO_G4"), (r"FI?LTRO(_SUCIO)?_F8$", "FILTRO_F8"),
    (r"FI?LTRO(_SUCIO)?_H[_.]?13$", "FILTRO_H13"),
    (r"^EST_CIRC_SERP_FRIO$", "FLUJO_FRIO"), (r"^EST_CIRC_SERP_CALOR$", "FLUJO_CALOR"),
    (r"^ON_INY$", "ON_INY"), (r"^ON_RET$", "ON_RET"), (r"^ON_HUM(IDIF)?$", "ON_HUM"),
    (r"^ON_PRESION$", "ON_PRESION"),
    (r"^EST_INY$", "EST_INY"), (r"^EST_RET$", "EST_RET"), (r"^EST_HUM$", "EST_HUM"),
    (r"^(FALLA_FUNC_INY|ALARMA_PRESION_INY)$", "FALLA_FUNC_INY"),
    (r"^(FALLA_FUNC_RET|ALARMA_PRESION_RET)$", "FALLA_FUNC_RET"),
    (r"^HORARIO(_UMA)?$", "HORARIO"),
    (r"^MANDO_GENERAL$", "MANDO_GENERAL"), (r"^MANDO_INY$", "MANDO_INY"), (r"^MANDO_RET$", "MANDO_RET"),
    (r"^HORAS_DE_FUNCIONAMIENTO_INY$", "HORAS_INY"), (r"^HORAS_DE_FUNCIONAMIENTO_RET$", "HORAS_RET"),
    (r"^HORAS_DE_FUNCIONAMIENTO_HUM$", "HORAS_HUM"),
    (r"^TEMP_SP$", "TEMP_SP"), (r"^TEMP_BANDA_MUERTA$", "TEMP_BANDA"),
    (r"^SP_HUMEDAD$", "HUM_SP"), (r"^SP_HUM_BANDA$", "HUM_BANDA"),
    (r"^TEMPERATU[RT]A_AMBIENTE$", "TEMP_AMB"), (r"^HUMEDAD_AMBIENTE$", "HUM_AMB"),
    (r"^PRESION_AMB1?$", "PRES_AMB"), (r"^PRESION_AMB2$", "PRES_AMB2"),
    (r"^PRESION_AMBIENTE$", "PRES_AMB_B"), (r"^SP_PRESION_AMBIENTE1?$", "PRES_SP"),
    (r"^SP_SDP_INY$", "SP_SDP_INY"), (r"^SP_SDP_RET$", "SP_SDP_RET"),
    (r"^SP_VDF_INY$", "SP_VDF_INY"), (r"^SP_VDF_RET$", "SP_VDF_RET"),
    (r"^SP_VDF_RET_AMB$", "SP_VDF_RET_AMB"),
    (r"^TEMPERATURA_(PABELLON|SALA).*", "TEMP_SALA"), (r"^CO2$", "CO2"),
    (r"^TEMPERATURA_EXTERIOR$", "TEMP_EXT"),
]
# Señales que se guardan en el historial (cada 5 min).
HISTORIAL = {"TEMP_AMB", "HUM_AMB", "PRES_AMB", "TEMP_SALA", "SDP_INY", "SDP_RET", "TEMP_INY",
             "TEMP_RET", "TEMP_AF_INY", "TEMP_AC_INY", "MOD_VDF_INY", "MOD_VDF_RET", "CO2"}


def arreglar(texto):
    try:
        return texto.encode("latin-1").decode("utf-8")
    except (UnicodeError, AttributeError):
        return texto or ""


def normalizar(nombre):
    t = arreglar(nombre).upper().replace("Ó", "O").replace("Í", "I")
    t = re.sub(r"[\s.]+(?=\d)", ".", t)
    t = re.sub(r"B1\.\d+", "", t)
    t = re.sub(r"[\s\-]+", "_", t)
    for largo, corto in (("INYECCION", "INY"), ("INYECTOR", "INY"), ("RETORNO", "RET"),
                         ("HUMECTADOR", "HUM"), ("HUMID", "HUM")):
        t = re.sub(rf"(?<![A-Z]){largo}(?![A-Z])", corto, t)
    t = t.replace("TEMPERATUTA", "TEMPERATURA")
    return re.sub(r"_+", "_", t).strip("_.")


def clasificar(ip, t, inst, nombre):
    """Devuelve dict con grupo/subgrupo/señal o None si no se sondea."""
    n = arreglar(nombre).upper()
    if t not in TIPOS or re.match(r"^BI_\d+$", n) or "RESET_ALARM" in n or "SENSORES BINARIA" in n:
        return None
    m = re.search(r"B1\.(\d+)", n)
    if m:
        senal = normalizar(nombre)
        for patron, s in SENALES:
            if re.search(patron, senal):
                return {"grupo": "uma", "uma": f"B1.{m[1]}", "senal": s}
        return {"grupo": "uma", "uma": f"B1.{m[1]}", "senal": senal}
    if m := re.match(r"(TEMP|HUM)_PABELLON_(\d)", n):
        return {"grupo": "sala", "sala": f"P{m[2]}", "senal": "TEMP" if m[1] == "TEMP" else "HUM"}
    if m := re.match(r"ALARMA (OXIG|AIRE|VACIO|N2) TDG[ _](\d+)", n):
        gas = {"OXIG": "O2", "AIRE": "AIRE", "VACIO": "VACIO", "N2": "N2"}[m[1]]
        return {"grupo": "gas", "tdg": f"TDG {m[2]}", "senal": gas}
    if m := re.match(r"ALARMA (GENERAL|BATERIA|AUTO|SOBRECARGA|BYPASS) UPS[ _]?(?:(P\d)|P)?[ _]?(\d+)", n):
        base = int(m[3])
        ups = f"UPS {m[2] or 'P2'}-{base - ['GENERAL','BATERIA','AUTO','SOBRECARGA','BYPASS'].index(m[1])}"
        return {"grupo": "ups", "ups": ups, "senal": m[1]}
    if m := re.match(r"ALARM_(GENER|BATERIA|AUTO|SOBREC|BYPASS)_(1\.2\.\d)", n):
        s = {"GENER": "GENERAL", "SOBREC": "SOBRECARGA"}.get(m[1], m[1])
        return {"grupo": "ups", "ups": f"UPS {m[2]}", "senal": s}
    if m := re.match(r"EP (\d+) (.*)", n):
        s = m[2].replace("ESTADO DE FUNCIONAMIENTO", "FUNCIONAMIENTO").replace(" ", "_")
        return {"grupo": "precision", "equipo": f"EP {m[1]}", "senal": s.rstrip("_BM").rstrip("_")}
    if m := re.match(r"EST_CKTO_(.*)", n):
        return {"grupo": "circuito", "senal": m[1]}
    if m := re.match(r"(?:ACT_MOTOR|TEMP)_P\d_\d+_SCAR_(\d)", n):
        return {"grupo": "scar", "scar": f"SCAR {m[1]}", "senal": "ACTUADOR" if n.startswith("ACT") else "TEMP"}
    if m := re.match(r"SP_TEMP(_BANDA)?_SCAR_(\d)", n):
        return {"grupo": "scar", "scar": f"SCAR {m[2]}", "senal": "TEMP_BANDA" if m[1] else "TEMP_SP"}
    if m := re.match(r"(?:SENSOR|SONDA)_CO2_(P\d_\d+)", n):
        return {"grupo": "ambiente", "senal": f"CO2 {m[1]}"}
    if re.match(r"(TEMP_P2_17|HUM_P2_15|DIF_PRESION_P2_16|TCC-Q2-01_(TEMP|HUM)_P1_\d+)", n):
        return {"grupo": "ambiente", "senal": n.replace("TCC-Q2-01_", "")}
    if m := re.search(r"(?:EST_)?(FUNC|FUERA DE SERVICIO|MANTENIMIENTO|AVERIA)_?\s?(L\d)", n):
        return {"grupo": "equipo_l", "equipo": m[2], "senal": m[1].replace("FUERA DE SERVICIO", "FUERA_SERVICIO")}
    if n.startswith("VEC_PA_1") or "VEC PA.1" in n:
        s = re.sub(r"VEC_PA_?\.?1_?|ALARMA |VEC PA\.1", "", n).strip("_ ").replace(" ", "_")
        return {"grupo": "extractor", "equipo": "VEC PA.1", "senal": s or "ESTADO"}
    return {"grupo": "otro", "senal": n}


def generar(escaneo):
    puntos, vistos = [], {}
    equipos = escaneo["equipos"]
    for ip in PREFERENCIA + [i for i in equipos if i not in PREFERENCIA]:
        dev = equipos.get(ip) or {}
        for o in dev.get("objetos", []):
            t, inst = o["t"], o["i"]
            nombre = CORRECCIONES.get((ip, t, inst), o.get("name") or "")
            desc = arreglar(o.get("desc") or "")
            # En Q2-02-525A hay objetos llamados "ALARMA VDF INY B1.6" que en realidad son el
            # estado de marcha (texto activo ENCENDIDO). La descripción dice la señal real.
            if nombre.upper().startswith("ALARMA VDF") and re.match(r"B1\.\d+_EST_(RES|VDF)_", desc):
                nombre = desc
            c = clasificar(ip, t, inst, nombre)
            if c is None:
                continue
            if c["grupo"] == "uma":
                k = (c["uma"], c["senal"])
                if k in vistos:
                    continue
                vistos[k] = ip
            p = {"clave": f'{ip.rsplit(".", 1)[1]}-{t}-{inst}', "ip": ip, "tipo": TIPOS[t],
                 "inst": inst, "nombre": arreglar(nombre).strip(), **c}
            if t in ("AI", "AO", "AV"):
                p["unidad"] = UNIDADES.get(o.get("units"), "")
            if t == "MV" and isinstance(o.get("states"), list):
                p["estados"] = [arreglar(s) for s in o["states"]]
            if t in ("BI", "BO", "BV") and o.get("active"):
                p["activo"] = arreglar(str(o["active"])).strip()
            if c.get("senal") in HISTORIAL or c["grupo"] in ("sala", "ambiente", "precision") \
                    and t in ("AI", "AV") and "HORAS" not in c["senal"]:
                p["hist"] = True
            puntos.append(p)
    return puntos


def main():
    origen = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "herramientas", "escaneo_demo.json")
    with open(origen, encoding="utf-8") as f:
        escaneo = json.load(f)
    puntos = generar(escaneo)
    destino = os.path.join(RAIZ, "config", "puntos.json")
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(puntos, f, ensure_ascii=False, indent=1)
    controladores = {ip: {k: d.get(k) for k in ("name", "model", "fw", "device")}
                     for ip, d in escaneo["equipos"].items()}
    with open(os.path.join(RAIZ, "config", "controladores_escaneo.json"), "w", encoding="utf-8") as f:
        json.dump({"ts": escaneo.get("ts"), "controladores": controladores}, f, ensure_ascii=False, indent=1)
    from collections import Counter
    print(len(puntos), "puntos →", destino)
    print(Counter(p["grupo"] for p in puntos))


if __name__ == "__main__":
    main()
