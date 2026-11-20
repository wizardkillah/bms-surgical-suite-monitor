"""Descubrimiento BACnet de solo lectura: object-list completo + propiedades de cada objeto."""
import json, socket, struct, sys, time
from concurrent.futures import ThreadPoolExecutor

LOCAL_IP = "192.0.2.245"
PORT = 47808
TYPES = {0: "AI", 1: "AO", 2: "AV", 3: "BI", 4: "BO", 5: "BV", 8: "DEV", 13: "MI", 14: "MO",
         19: "MV", 10: "FILE", 15: "NC", 17: "SCHED", 6: "CAL", 20: "TL", 16: "PROG", 23: "ACC", 12: "LOOP"}
PROPS = {"name": 77, "pv": 85, "units": 117, "desc": 28, "flags": 111, "oos": 81,
         "active": 4, "inactive": 46, "states": 110, "rel": 87, "hi": 45, "lo": 59, "cov": 22}


def req(otype, inst, prop, idx=None, iid=1):
    apdu = bytes([0x00, 0x05, iid & 0xFF, 0x0C, 0x0C]) + struct.pack(">I", (otype << 22) | (inst & 0x3FFFFF))
    apdu += bytes([0x19, prop]) if prop < 256 else bytes([0x1A]) + struct.pack(">H", prop)
    if idx is not None:
        apdu += bytes([0x29, idx]) if idx < 256 else bytes([0x2A]) + struct.pack(">H", idx)
    npdu = bytes([0x01, 0x04])
    return bytes([0x81, 0x0A]) + struct.pack(">H", 4 + len(npdu) + len(apdu)) + npdu + apdu


def tags(buf, i, end):
    """Decodifica etiquetas de aplicación entre i y end."""
    out = []
    while i < end:
        b = buf[i]; num = b >> 4; ctx = b & 0x08; ln = b & 0x07; i += 1
        if num == 0x0F:
            num = buf[i]; i += 1
        if ln == 5:
            ln = buf[i]; i += 1
            if ln == 254: ln = struct.unpack(">H", buf[i:i+2])[0]; i += 2
        elif ln in (6, 7):
            continue  # apertura/cierre
        if ctx:
            out.append(("ctx", buf[i:i+ln])); i += ln; continue
        if num == 1:
            out.append(bool(ln)); continue
        data = buf[i:i+ln]; i += ln
        if num == 0: out.append(None)
        elif num == 2: out.append(int.from_bytes(data, "big"))
        elif num == 3: out.append(int.from_bytes(data, "big", signed=True))
        elif num == 4: out.append(round(struct.unpack(">f", data)[0], 3))
        elif num == 5: out.append(struct.unpack(">d", data)[0])
        elif num == 7:
            enc = data[0]; raw = data[1:]
            out.append(raw.decode("utf-16-be" if enc == 4 else "latin-1", "replace").strip("\x00"))
        elif num == 8: out.append("bits:" + "".join(f"{x:08b}" for x in data[1:])[:16 - 0])
        elif num == 9: out.append(int.from_bytes(data, "big"))
        elif num == 12:
            v = int.from_bytes(data, "big"); out.append((v >> 22, v & 0x3FFFFF))
        else: out.append(data.hex())
    return out


def ask(sock, ip, otype, inst, prop, idx=None, tries=2, timeout=1.2):
    for t in range(tries):
        iid = (int(time.monotonic() * 1000) + t) & 0xFF
        sock.sendto(req(otype, inst, prop, idx, iid), (ip, PORT))
        sock.settimeout(timeout)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                break
            # localizar APDU
            if len(data) < 6: continue
            npdu_ctrl = data[5]; i = 6
            if npdu_ctrl & 0x08:  # SNET presente
                i += 3 + data[i+2]
                i += 1 if npdu_ctrl & 0x20 else 0
            apdu = data[i:]
            if not apdu: continue
            kind = apdu[0] >> 4
            if apdu[1] != iid: continue
            if kind == 5:  return ("error", None)
            if kind == 4:  return ("error", "abort")
            if kind != 3:  continue
            if apdu[0] & 0x08:
                return ("error", "segmentado")
            j = 3
            if apdu[j] != 0x0C: return ("error", None)
            j += 5
            j += 2 if apdu[j] == 0x19 else 3
            if apdu[j] in (0x29, 0x2A): j += 2 if apdu[j] == 0x29 else 3
            if apdu[j] != 0x3E: return ("error", None)
            try:
                return ("ok", tags(apdu, j + 1, len(apdu) - 1))
            except Exception:
                return ("error", "parse")
    return ("timeout", None)


def scan(ip):
    try:
        return _scan(ip)
    except Exception as exc:
        import traceback
        return ip, {"error": traceback.format_exc()}


def _scan(ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.bind((LOCAL_IP, 0))
    st, v = ask(sock, ip, 8, 4194303, 75)
    if st != "ok":
        return ip, {"error": "sin respuesta al objeto device"}
    dev = v[0][1]
    info = {"device": dev}
    for k, p in (("name", 77), ("model", 70), ("vendor", 121), ("fw", 44), ("desc", 28), ("location", 58)):
        s, val = ask(sock, ip, 8, dev, p); info[k] = val[0] if s == "ok" and val else None
    s, val = ask(sock, ip, 8, dev, 76, 0)
    n = val[0] if s == "ok" else 0
    objs = []
    for k in range(1, n + 1):
        s, val = ask(sock, ip, 8, dev, 76, k)
        if s == "ok" and val and isinstance(val[0], tuple) and isinstance(val[0][0], int):
            objs.append(val[0])
        else:
            info.setdefault("lista_rara", []).append([k, s, repr(val)[:80]])
    info["n_objetos"] = n
    rows = []
    for otype, inst in objs:
        if otype == 8: continue
        row = {"t": TYPES.get(otype, str(otype)), "i": inst, "otype": otype}
        wanted = ["name", "pv", "desc", "flags", "oos"]
        if otype in (0, 1, 2): wanted += ["units", "hi", "lo"]
        if otype in (3, 4, 5): wanted += ["active", "inactive"]
        if otype in (13, 14, 19): wanted += ["states"]
        for key in wanted:
            s, val = ask(sock, ip, otype, inst, PROPS[key], tries=1)
            if s == "ok":
                row[key] = val if key == "states" else (val[0] if val else None)
        rows.append(row)
    info["objetos"] = rows
    sock.close()
    return ip, info


if __name__ == "__main__":
    ips = sys.argv[2:]
    with ThreadPoolExecutor(8) as ex:
        res = dict(ex.map(scan, ips))
    json.dump({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "equipos": res},
              open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    for ip, d in res.items():
        print(ip, d.get("name"), d.get("model"), "objetos:", d.get("n_objetos"), d.get("error", ""))
