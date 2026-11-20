"""python -m unittest discover tests — sin red: usa los valores de ejemplo anonimizados."""
import http.client
import json
import os
import sys
import threading
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "herramientas"))
import generar_puntos as gen  # noqa: E402
from pabellones import servidor  # noqa: E402
from pabellones.demo import ClienteDemo  # noqa: E402
from pabellones.monitor import Monitor  # noqa: E402


class Cliente(ClienteDemo):
    """Demo sin variación + valores forzados + controladores caídos."""
    forzados, caidos = {}, set()

    def leer(self, ip, tipo, inst, propiedad="present-value"):
        if ip in self.caidos:
            return None
        if (ip, tipo, inst) in self.forzados:
            return self.forzados[(ip, tipo, inst)]
        from pabellones.demo import CORTO
        return self._valores.get((ip, CORTO.get(tipo, tipo), inst))


def monitor():
    Cliente.forzados, Cliente.caidos = {}, set()
    return Monitor(":memory:", fabrica_cliente=Cliente)


class Generador(unittest.TestCase):
    def test_clasificacion(self):
        c = gen.clasificar
        self.assertEqual(c("x", "BI", 1, "ALARMA OXIG TDG 04"), {"grupo": "gas", "tdg": "TDG 04", "senal": "O2"})
        self.assertEqual(c("x", "BI", 1, "ALARMA SOBRECARGA UPS P 454")["ups"], "UPS P2-451")
        self.assertEqual(c("x", "BI", 1, "ALARMA BYPASS UPS P1 430")["ups"], "UPS P1-426")
        self.assertEqual(c("x", "AV", 1, "B1.4_TEMPERATUTA AMBIENTE")["senal"], "TEMP_AMB")
        self.assertEqual(c("x", "BI", 1, "ALARMA FLTRO SUCIO H13 B1.9")["senal"], "FILTRO_H13")
        self.assertIsNone(c("x", "BI", 41, "BI_41"))
        self.assertIsNone(c("x", "BV", 21, "B1.2_RESET_ALARM_B1.2"))

    def test_alarma_vdf_mal_nombrada_usa_la_descripcion(self):
        with open(os.path.join(RAIZ, "config", "puntos.json"), encoding="utf-8") as f:
            puntos = json.load(f)
        b16 = {p["senal"]: p for p in puntos if p.get("uma") == "B1.6"}
        self.assertEqual(b16["EST_VDF_INY"]["clave"], "24-BI-210")     # "ALARMA VDF INY B1.6" = marcha
        self.assertEqual(b16["FALLA_VDF_INY"]["clave"], "24-BI-211")


class Modelo(unittest.TestCase):
    def setUp(self):
        self.m = monitor()
        self.m.ciclo()
        self.m.ciclo()
        self.e = self.m.instantanea()

    def test_pabellones_y_controladores(self):
        salas = {s["id"]: s for s in self.e["salas"]}
        self.assertEqual(len([s for s in salas.values() if s["tipo"] == "pabellon"]), 7)
        self.assertAlmostEqual(salas["P1"]["temp"]["valor"], 21.3, delta=0.5)
        self.assertAlmostEqual(salas["P1"]["temp"]["sp"], 21.7, delta=0.5)
        self.assertEqual(salas["SIPA"]["hum"]["estado"], "sensor")       # 0,122 % en el escaneo
        en_linea = {c["ip"]: c["en_linea"] for c in self.e["controladores"]}
        self.assertFalse(en_linea["192.0.2.27"])
        self.assertTrue(en_linea["192.0.2.22"])

    def test_alarmas_del_escaneo(self):
        msgs = [a["mensaje"] for a in self.e["alarmas"]]
        self.assertIn("Pabellón 2 · UMA B1.2: filtro G4 sucio", msgs)
        self.assertTrue(any("UPS reportan bypass" in t for t in msgs))
        self.assertFalse(any("variador" in t for t in msgs), msgs)      # no falsa falla en B1.6
        self.assertEqual(self.e["umas"]["B1.2"]["aire"]["inyeccion"]["estado"], "sensor")  # -60 °C

    def test_confirmacion_y_normalizacion(self):
        m = self.m
        Cliente.forzados[("192.0.2.28", "binaryInput", 1)] = 1     # alarma O2 TDG 04
        m.ciclo()
        self.assertNotIn("gas-TDG 04-O2", m.activas)                   # un ciclo no basta
        m.ciclo()
        self.assertEqual(m.activas["gas-TDG 04-O2"]["nivel"], "alarma")
        Cliente.caidos.add("192.0.2.28")
        m.ciclo()
        self.assertIn("gas-TDG 04-O2", m.activas)                      # congelada sin datos
        self.assertIn("com-192.0.2.28", m.activas)
        Cliente.caidos.clear(); Cliente.forzados.clear()
        m.ciclo()
        self.assertNotIn("gas-TDG 04-O2", m.activas)
        self.assertTrue(any(e["tipo"] == "NORMALIZADA" for e in m.eventos()))

    def test_temperatura_fuera_de_rango(self):
        m = self.m
        Cliente.forzados[("192.0.2.24", "analogValue", 112)] = 27.5    # B1.3 temperatura ambiente
        m.ciclo(); m.ciclo()
        a = m.activas["P3-temp"]
        self.assertEqual(a["nivel"], "alarma")                          # > 2 °C sobre el máximo
        Cliente.forzados[("192.0.2.24", "analogValue", 112)] = 24.8
        m.ciclo(); m.ciclo()
        self.assertEqual(m.activas["P3-temp"]["nivel"], "aviso")

    def test_historial(self):
        m = self.m
        m._guardar(m.ts_ciclo)
        clave = self.e["salas"][0]["temp"]["clave"]
        h = m.historial(m.ts_ciclo - 3600, m.ts_ciclo + 1, [clave])
        self.assertEqual(len(h["series"][clave]), 1)


class Documentos(unittest.TestCase):
    """Informe técnico y Excel, general y por sala, con historial sintético de 2 días."""

    @classmethod
    def setUpClass(cls):
        cls.m = monitor()
        cls.m.ciclo()
        ahora = cls.m.ts_ciclo
        p1 = cls.m.instantanea()["salas"][0]
        cls.clave_t = p1["temp"]["clave"]
        filas = []
        for i in range(576):                                   # 2 días cada 5 min
            ts = ahora - 2 * 86400 + i * 300
            filas.append((ts, cls.clave_t, 25.5 if i < 58 else -60.0 if i == 100 else 21.0))
            filas.append((ts, p1["hum"]["clave"], 50.0))
        cls.m.db.executemany("INSERT OR REPLACE INTO lecturas VALUES (?,?,?)", filas)
        cls.m.db.commit()
        cls.desde, cls.hasta = ahora - 2 * 86400, ahora + 1

    def test_estadisticas_excluyen_sensor_y_calculan_rango(self):
        e = self.m.estadisticas(self.desde, self.hasta, {self.clave_t: (18, 24)}, {self.clave_t: (-10, 60)})[self.clave_t]
        self.assertEqual(e["descartados"], 1)
        self.assertEqual(e["min"], 21.0)
        self.assertAlmostEqual(e["en_rango"], 517 / 575, places=3)

    def test_informe_general_y_por_sala(self):
        from pabellones import informe
        general = informe.generar(self.m, self.desde, self.hasta)
        self.assertIn("Pabellón 7", general)
        self.assertIn("Gases clínicos", general)
        self.assertIn("<svg", general)
        sala = informe.generar(self.m, self.desde, self.hasta, "P1")
        self.assertIn("Informe técnico · Pabellón 1", sala)
        self.assertNotIn("Pabellón 7", sala)
        self.assertIn("solo el 90 %", sala)                        # 517/575 dentro de 18–24 °C
        with self.assertRaises(ValueError):
            informe.generar(self.m, self.desde, self.hasta, "X9")

    def test_excel_general_y_por_sala(self):
        import io
        from openpyxl import load_workbook
        from pabellones import excel
        datos, nombre = excel.generar(self.m, self.desde, self.hasta)
        wb = load_workbook(io.BytesIO(datos))
        self.assertTrue(nombre.startswith("pabellones_general_"))
        for hoja in ("Resumen", "Observaciones", "Historial", "Gráfico T°", "Estadísticas", "UMA", "Gases y UPS",
                     "Eventos", "Puntos BACnet", "Registro de rondas"):
            self.assertIn(hoja, wb.sheetnames)
        datos, nombre = excel.generar(self.m, self.desde, self.hasta, "P1")
        wb = load_workbook(io.BytesIO(datos))
        self.assertIn("_p1_", nombre)
        self.assertNotIn("Gases y UPS", wb.sheetnames)
        h = wb["Historial"]
        valores = [h.cell(row=r, column=2).value for r in range(3, h.max_row + 1)]
        self.assertNotIn(-60.0, valores)                            # sensor dañado en blanco
        self.assertEqual(wb["Registro de rondas"]["C6"].value, "Pabellón 1")


class Servidor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        servidor.MON = monitor()
        servidor.MON.ciclo()
        cls.srv = servidor.Servidor(("127.0.0.1", 0), servidor.Manejador)
        cls.puerto = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def pedir(self, metodo, ruta, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=5)
        c.request(metodo, ruta, headers={"Host": host or f"localhost:{self.puerto}"})
        r = c.getresponse()
        return r.status, r.read()

    def test_estado_e_index(self):
        st, cuerpo = self.pedir("GET", "/api/estado")
        self.assertEqual(st, 200)
        self.assertEqual(len(json.loads(cuerpo)["salas"]), 11)
        st, cuerpo = self.pedir("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"Hospital Demo", cuerpo)        # logo incrustado
        self.assertNotIn(b"@@LOGO_SVG@@", cuerpo)

    def test_descargas(self):
        st, cuerpo = self.pedir("GET", "/api/informe?sala=P3&horas=24")
        self.assertEqual(st, 200)
        self.assertIn("Pabellón 3".encode(), cuerpo)
        st, cuerpo = self.pedir("GET", "/api/excel?desde=2026-11-01&hasta=2026-11-16")
        self.assertEqual(st, 200)
        self.assertEqual(cuerpo[:2], b"PK")
        self.assertEqual(self.pedir("GET", "/api/excel?sala=../x")[0], 400)
        self.assertEqual(self.pedir("GET", "/api/informe?desde=2026-11-16&hasta=2026-11-01")[0], 400)

    def test_solo_lectura_y_host(self):
        self.assertEqual(self.pedir("POST", "/api/estado")[0], 405)
        self.assertEqual(self.pedir("GET", "/api/estado", host="evil.example")[0], 403)
        self.assertEqual(self.pedir("GET", "/../config/pabellones.json")[0], 404)


if __name__ == "__main__":
    unittest.main()
