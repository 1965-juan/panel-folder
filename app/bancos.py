"""Bancos: subir el Excel de movimientos del banco (CaixaBank y similares) y meterlo en Odoo
en la cuenta correcta, sin duplicar lo que ya está; y ver lo que queda sin conciliar con sugerencias.

Formato CaixaBank (.xls): hoja "Movimientos_cuenta_0210045" con columnas
Fecha | Fecha valor | Movimiento | Más datos | Importe | Saldo (lo más nuevo arriba).
Se guarda igual que lo hace Mayte al importar en Odoo: concepto = Movimiento, empresa = Más datos.
"""
import datetime as dt
import io
import re

from fastapi import Request, HTTPException, UploadFile, File, Form


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip().replace("€", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _fecha(v, datemode=0):
    if isinstance(v, (int, float)) and v > 20000:
        return (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(v))).date().isoformat()
    s = str(v or "").strip()
    m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", s)
    if m:
        d, mo, y = m.groups()
        y = int(y) + (2000 if len(y) == 2 else 0)
        return dt.date(y, int(mo), int(d)).isoformat()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else None


def leer_extracto(nombre, datos):
    """Devuelve (cuenta_hint, filas) con filas = [{fecha, concepto, empresa, importe, saldo}]."""
    tablas = []
    low = (nombre or "").lower()
    if low.endswith(".xls") or datos[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        try:
            from .vendor import xlrd
        except ImportError:
            import xlrd
        wb = xlrd.open_workbook(file_contents=datos)
        for sh in wb.sheets():
            tablas.append((sh.name, [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]))
    elif low.endswith(".xlsx") or datos[:2] == b"PK":
        import zipfile
        import xml.etree.ElementTree as ET
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        z = zipfile.ZipFile(io.BytesIO(datos))
        ss = []
        if "xl/sharedStrings.xml" in z.namelist():
            ss = ["".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        wbx = ET.fromstring(z.read("xl/workbook.xml"))
        nombre_hoja = (wbx.find(".//m:sheet", ns).get("name") if wbx.find(".//m:sheet", ns) is not None else "")
        filas = []

        def _ncol(letras):
            n = 0
            for ch in letras:
                n = n * 26 + ord(ch) - 64
            return n - 1
        for row in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter("{%s}row" % ns["m"]):
            fila = {}
            for c in row.findall("m:c", ns):
                col = _ncol(re.match(r"[A-Z]+", c.get("r")).group(0))
                t = c.get("t")
                v = c.find("m:v", ns)
                val = v.text if v is not None else ""
                if t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
                elif t == "s" and val:
                    val = ss[int(val)]
                elif t not in ("s", "str") and val and re.fullmatch(r"-?\d+(\.\d+)?([eE]-?\d+)?", val):
                    val = float(val)
                fila[col] = val
            filas.append([fila.get(i, "") for i in range(max(fila) + 1)] if fila else [])
        tablas.append((nombre_hoja, filas))
    else:  # CSV
        import csv
        txt = datos.decode("utf-8-sig", errors="ignore")
        if txt.count(";") > txt.count(","):
            rows = list(csv.reader(io.StringIO(txt), delimiter=";"))
        else:
            rows = list(csv.reader(io.StringIO(txt)))
        tablas.append(("", rows))

    for hoja, rows in tablas:
        # cabecera: fila con "Fecha" e "Importe"
        def _esfecha(x):
            x = str(x).strip().lower()
            return "fecha" in x or x.startswith("f. ") or x.startswith("f.")
        hi = next((i for i, r in enumerate(rows[:15]) if any(_esfecha(x) for x in r) and any("importe" in str(x).lower() for x in r)), None)
        if hi is None:
            continue
        cab = [str(x).strip().lower() for x in rows[hi]]

        def col(*claves, evitar=()):
            for i, h in enumerate(cab):
                if any(k in h for k in claves) and not any(e in h for e in evitar):
                    return i
            return None
        cf = col("fecha", evitar=("valor",))
        if cf is None:
            cf = next((i for i, h in enumerate(cab) if h.startswith("f.") and "valor" not in h), None)
        cm, cd, ci, cs = col("movimiento", "concepto", "descripci", "etiqueta"), col("más datos", "mas datos", "observ", "detalle"), col("importe"), col("saldo")
        filas = []
        for r in rows[hi + 1:]:
            if ci is None or ci >= len(r):
                continue
            imp = _num(r[ci])
            f = _fecha(r[cf]) if cf is not None and cf < len(r) else None
            if imp is None or not f:
                continue
            filas.append({"fecha": f, "concepto": str(r[cm]).strip() if cm is not None else "", "empresa": str(r[cd]).strip() if cd is not None and cd < len(r) else "",
                          "importe": round(imp, 2), "saldo": _num(r[cs]) if cs is not None and cs < len(r) else None})
        m = re.search(r"(\d{6,})", hoja or "") or re.search(r"(\d{6,})", nombre or "")
        return (m.group(1) if m else ""), filas
    raise ValueError("No encuentro la tabla de movimientos (columnas Fecha e Importe)")


BANCOS = {"bbva": ("bbva", "bilbao"), "sabadell": ("sabadell",), "santander": ("santander",), "caixa": ("caixa",)}
ENTIDAD = {"bbva": "0182", "sabadell": "0081", "santander": "0049", "caixa": "2100"}  # código de banco dentro del IBAN
EMPRESAS = ("distriplus", "logipaper", "ingraf", "fermasa")


def _por_nombre(nombre, js):
    """Sin número de cuenta en el Excel: adivina por el nombre del archivo (banco + empresa), p. ej. "bbva_logipaper.xlsx"."""
    n = (nombre or "").lower()
    banco = next((k for k in BANCOS if k in n), None)
    if not banco:
        return None
    cand = [x for x in js if any(b in x["name"].lower() for b in BANCOS[banco])
            or (len(x["iban_digitos"]) >= 8 and x["iban_digitos"][2:6] == ENTIDAD[banco])]
    emp = next((e for e in EMPRESAS if e in n), None)
    if emp:
        cand = [x for x in cand if emp in x["company_id"][1].lower()]
        if emp == "fermasa":
            cand = [x for x in cand if ("inmob" in n) == ("inmob" in x["company_id"][1].lower())]
    if len(cand) > 1:  # varias cuentas del mismo banco y empresa: probar con palabras del archivo (general, imprenta…)
        pal = [w for w in re.findall(r"[a-z]{5,}", n) if w not in EMPRESAS and w not in BANCOS and w not in ("caixa", "xlsx")]
        fino = [x for x in cand if any(w in x["name"].lower() for w in pal)]
        cand = fino or cand
    return cand[0] if len(cand) == 1 else None


def setup(app, *, odoo, current_user, scope, guard, odoo_url):

    def diarios(cids):
        js = odoo.search_read("account.journal", [("type", "=", "bank")], ["name", "company_id", "bank_acc_number"], company_ids=cids)
        for j in js:
            j["iban_digitos"] = re.sub(r"\D", "", j.get("bank_acc_number") or "")
        return js

    @app.get("/api/bancos/diarios")
    def api_diarios(request: Request):
        u = current_user(request)
        cids, _ = scope(u, "all")
        return guard(lambda: [{"id": j["id"], "nombre": j["name"], "empresa": j["company_id"][1], "iban": j.get("bank_acc_number") or ""} for j in diarios(cids)])

    @app.post("/api/bancos/leer")
    async def api_leer(request: Request, archivo: UploadFile = File(...), diario: str = Form("")):
        """Lee el Excel, adivina la cuenta y marca qué movimientos ya están en Odoo. No escribe nada."""
        u = current_user(request)
        cids, _ = scope(u, "all")
        datos = await archivo.read()

        def run():
            try:
                pista, filas = leer_extracto(archivo.filename, datos)
            except Exception as e:
                raise HTTPException(400, f"No he podido leer el archivo: {e}")
            if not filas:
                raise HTTPException(400, "El archivo no tiene movimientos")
            js = diarios(cids)
            j = None
            if diario:
                j = next((x for x in js if str(x["id"]) == str(diario)), None)
            if not j and pista:
                cand = [x for x in js if x["iban_digitos"] and x["iban_digitos"].endswith(pista[-7:])]
                j = cand[0] if len(cand) == 1 else None
            if not j:
                j = _por_nombre(archivo.filename, js)
            out = {"archivo": archivo.filename, "pista": pista, "diario": None, "filas": filas,
                   "diarios": [{"id": x["id"], "nombre": x["name"], "empresa": x["company_id"][1], "iban": x.get("bank_acc_number") or ""} for x in js]}
            if not j:
                return out
            out["diario"] = {"id": j["id"], "nombre": j["name"], "empresa": j["company_id"][1], "iban": j.get("bank_acc_number") or ""}
            fechas = sorted({f["fecha"] for f in filas})
            ex = odoo.search_read("account.bank.statement.line", [("journal_id", "=", j["id"]), ("date", ">=", fechas[0]), ("date", "<=", fechas[-1])],
                                  ["date", "amount", "payment_ref"], company_ids=[j["company_id"][0]])
            usados = set()
            for f in filas:
                hit = next((e for e in ex if e["id"] not in usados and e["date"] == f["fecha"] and abs(e["amount"] - f["importe"]) < 0.005
                            and (e.get("payment_ref") or "").strip().upper() == f["concepto"].upper()), None)
                if not hit:
                    hit = next((e for e in ex if e["id"] not in usados and e["date"] == f["fecha"] and abs(e["amount"] - f["importe"]) < 0.005), None)
                if hit:
                    usados.add(hit["id"])
                f["ya_en_odoo"] = bool(hit)
            return out
        return guard(run)

    @app.post("/api/bancos/importar")
    async def api_importar(request: Request):
        u = current_user(request)
        cids, _ = scope(u, "all")
        b = await request.json()
        jid = int(b["diario"])
        nuevas = [f for f in b.get("filas", []) if not f.get("ya_en_odoo")]

        def run():
            j = next((x for x in diarios(cids) if x["id"] == jid), None)
            if not j:
                raise HTTPException(403, "Cuenta no permitida")
            if not nuevas:
                return {"ok": True, "creadas": 0}
            cid = j["company_id"][0]
            # en el Excel lo más nuevo va arriba: al revés para Odoo
            orden = list(reversed(nuevas))
            fin = max(f["fecha"] for f in orden)
            lineas = [[0, 0, {"journal_id": jid, "date": f["fecha"], "payment_ref": f["concepto"] or f["empresa"] or "/",
                              "partner_name": f["empresa"] or False, "amount": f["importe"]}] for f in orden]
            vals = {"journal_id": jid, "name": f"{j['name'][:20]} {fin} (panel)", "date": fin, "line_ids": lineas}
            # saldos si el archivo los trae: inicial = saldo de la primera fila - su importe; final = saldo de la última
            if orden[0].get("saldo") is not None and orden[-1].get("saldo") is not None:
                vals["balance_start"] = round(orden[0]["saldo"] - orden[0]["importe"], 2)
                vals["balance_end_real"] = round(orden[-1]["saldo"], 2)
            try:
                st = odoo.call("account.bank.statement", "create", [cid], vals_list=[vals])
            except Exception:
                vals.pop("balance_start", None); vals.pop("balance_end_real", None)
                st = odoo.call("account.bank.statement", "create", [cid], vals_list=[vals])
            return {"ok": True, "creadas": len(lineas), "extracto": st[0] if isinstance(st, list) else st}
        return guard(run)

    @app.get("/api/bancos/pendientes")
    def api_pendientes(request: Request):
        """Movimientos sin conciliar por cuenta, con la factura que cuadra por importe si la hay."""
        u = current_user(request)
        cids, _ = scope(u, "all")

        def run():
            js = diarios(cids)
            ls = odoo.search_read("account.bank.statement.line", [("journal_id", "in", [j["id"] for j in js]), ("is_reconciled", "=", False)],
                                  ["journal_id", "date", "payment_ref", "partner_name", "partner_id", "amount", "company_id"],
                                  company_ids=cids, order="date desc", limit=300)
            out = []
            for l in ls:
                tipo = "out_invoice" if l["amount"] > 0 else "in_invoice"
                cand = odoo.search_read("account.move", [("move_type", "=", tipo), ("state", "=", "posted"), ("payment_state", "in", ["not_paid", "partial"]),
                                                         ("company_id", "=", l["company_id"][0]), ("amount_residual", ">=", abs(l["amount"]) - 0.01),
                                                         ("amount_residual", "<=", abs(l["amount"]) + 0.01)],
                                        ["name", "partner_id", "invoice_date", "amount_residual"], company_ids=cids, limit=5)
                nombre = (l.get("partner_name") or (l.get("partner_id") or [0, ""])[1] or "").upper()
                cand.sort(key=lambda c: -sum(1 for w in re.findall(r"\w{4,}", (c["partner_id"] or [0, ""])[1].upper()) if w in nombre))
                out.append({"id": l["id"], "cuenta": l["journal_id"][1], "fecha": l["date"], "concepto": l.get("payment_ref") or "",
                            "empresa": l.get("partner_name") or (l.get("partner_id") or [0, ""])[1] or "", "importe": l["amount"],
                            "sugerencias": [{"id": c["id"], "factura": c["name"], "cliente": (c["partner_id"] or [0, ""])[1], "fecha": c.get("invoice_date"),
                                             "pendiente": c["amount_residual"]} for c in cand[:3]]})
            return {"lineas": out, "odoo_url": odoo_url}
        return guard(run)
