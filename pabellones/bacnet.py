"""Cliente BACnet/IP crudo (UDP 47808) — sin dependencias externas.

Implementa ReadProperty simple (present-value / object-name / units / status-flags),
suficiente para leer las UMA, gases, UPS y sensores de los tableros de pabellones.
Derivado del cliente del dashboard de gases clinicos.
"""
import socket
import struct
import time

BACNET_PORT = 47808

TIPO_NUM = {
    "analogInput": 0, "analogOutput": 1, "analogValue": 2,
    "binaryInput": 3, "binaryOutput": 4, "binaryValue": 5,
    "multiStateValue": 19, "device": 8,
}

PROP = {"present-value": 85, "object-name": 77, "units": 117,
        "description": 28, "status-flags": 111}

# Enumeracion BACnetEngineeringUnits (solo las que aparecen en la planta).
# Ojo: en otro tablero las humedades estan configuradas con unidad 96
# (parts-per-million) aunque el valor es %HR; el dashboard usa la unidad del
# archivo de configuracion, no la del PLC.
UNIDADES = {
    29: "%HR", 53: "Pa", 62: "°C", 71: "h", 95: "", 96: "ppm", 98: "%",
}


class ClienteBacnet:
    """Un socket por instancia. No es thread-safe: usar uno por hilo."""

    def __init__(self, ip_local=None, timeout=1.0):
        self._invoke = 0
        self._timeout = timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if ip_local:
            try:
                self.sock.bind((ip_local, 0))
            except OSError:
                # La IP configurada ya no existe en este PC (paso con .250 -> .245):
                # mejor salir por la interfaz por defecto que no leer nada.
                pass
        self.sock.settimeout(timeout)

    def cerrar(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def _paquete(self, tipo, instancia, prop_id):
        self._invoke = (self._invoke + 1) % 255
        oid = struct.pack(">I", (tipo << 22) | (instancia & 0x3FFFFF))
        apdu = (bytes([0x00, 0x05, self._invoke, 0x0C, 0x0C]) + oid +
                bytes([0x19, prop_id]))
        npdu = bytes([0x01, 0x04])
        total = 4 + len(npdu) + len(apdu)
        return bytes([0x81, 0x0A]) + struct.pack(">H", total) + npdu + apdu

    @staticmethod
    def _valor(data):
        """Devuelve (clase, valor) del ComplexACK, o (None, None)."""
        if not data or len(data) < 8 or (data[6] & 0xF0) != 0x30:
            return None, None
        for i in range(6, len(data) - 2):
            if data[i] != 0x3E:          # opening tag 3
                continue
            tag = data[i + 1]
            num, largo = (tag >> 4) & 0xF, tag & 0x7
            ini = i + 2
            if largo == 5 and i + 2 < len(data):
                largo, ini = data[i + 2], i + 3
            if num == 4 and largo == 4 and len(data) >= ini + 4:
                return "float", round(struct.unpack(">f", data[ini:ini + 4])[0], 3)
            if num == 9 and largo == 1 and len(data) >= ini + 1:
                return "enum", data[ini]
            if num == 2 and 0 < largo <= 4 and len(data) >= ini + largo:
                return "uint", int.from_bytes(data[ini:ini + largo], "big")
            if num == 8 and len(data) >= ini + largo:   # bit string
                return "bits", data[ini:ini + largo]
            break
        return None, None

    @staticmethod
    def _texto(data):
        if not data or len(data) < 8 or (data[6] & 0xF0) != 0x30:
            return None
        for i in range(6, len(data) - 3):
            if data[i] != 0x3E:
                continue
            j = i + 1
            largo = data[j] & 0x7
            if largo == 5 and j + 1 < len(data):
                largo, ini = data[j + 1], j + 2
            else:
                ini = j + 1
            if ini >= len(data):
                break
            cod = data[ini]
            crudo = data[ini + 1:ini + largo]
            enc = "utf-8" if cod == 0 else "utf-16-be" if cod == 4 else "latin-1"
            txt = crudo.decode(enc, errors="replace").strip("\x00 \t\r\n")
            return txt or None
        return None

    @staticmethod
    def _apdu(data):
        """Normaliza la respuesta a BVLC + NPDU de 2 bytes + APDU.

        Devuelve (datos normalizados, invoke-id, tipo de PDU) o (None, None, None).
        """
        if len(data) < 8 or data[0] != 0x81 or data[4] != 0x01:
            return None, None, None
        ctrl, i = data[5], 6
        if ctrl & 0x20:                         # DNET + DLEN + DADR
            i += 3 + data[i + 2] if len(data) > i + 2 else len(data)
        if ctrl & 0x08:                         # SNET + SLEN + SADR
            i += 3 + data[i + 2] if len(data) > i + 2 else len(data)
        if ctrl & 0x20:                         # hop count
            i += 1
        if ctrl & 0x80 or len(data) < i + 2:    # mensaje de capa de red
            return None, None, None
        return data[:4] + b"\x01\x00" + data[i:], data[i + 1], data[i] & 0xF0

    def _vaciar(self):
        """Descarta respuestas atrasadas que quedaron en el socket."""
        self.sock.setblocking(False)
        try:
            while True:
                self.sock.recvfrom(1500)
        except OSError:
            pass
        finally:
            self.sock.settimeout(self._timeout)

    def leer(self, ip, tipo, instancia, propiedad="present-value"):
        """Lee una propiedad. Devuelve el valor decodificado o None.

        Solo acepta la respuesta a ESTA petición (misma IP e invoke-id). Antes se
        tomaba el primer datagrama: con el cliente permanente del monitor, una
        sola respuesta atrasada corría la cola en uno y cada punto pasaba a
        mostrar el valor del anterior, para siempre (pasó en clima el 03-11-2026).
        """
        num = TIPO_NUM[tipo] if isinstance(tipo, str) else tipo
        pkt = self._paquete(num, instancia, PROP[propiedad])
        invoke = self._invoke
        fin = time.monotonic() + self._timeout
        try:
            self._vaciar()
            self.sock.sendto(pkt, (ip, BACNET_PORT))
            while True:
                resto = fin - time.monotonic()
                if resto <= 0:
                    return None
                self.sock.settimeout(resto)
                crudo, origen = self.sock.recvfrom(1500)
                if origen[0] != ip:
                    continue
                data, inv, pdu = self._apdu(crudo)
                if data is None or inv != invoke:
                    continue                    # respuesta vieja u otra cosa
                if pdu != 0x30:                 # Error / Reject / Abort
                    return None
                break
        except Exception:
            return None
        finally:
            self.sock.settimeout(self._timeout)
        if propiedad in ("object-name", "description"):
            return self._texto(data)
        clase, valor = self._valor(data)
        if propiedad == "units" and valor is not None:
            return UNIDADES.get(valor, f"unit:{valor}")
        if propiedad == "status-flags" and clase == "bits":
            # byte 0 = bits sin usar; byte 1: in-alarm, fault, overridden, out-of-service
            b = valor[1] if len(valor) > 1 else 0
            return {"en_alarma": bool(b & 0x80), "falla": bool(b & 0x40),
                    "forzado": bool(b & 0x20), "fuera_servicio": bool(b & 0x10)}
        return valor
