"""Facturar albaranes (eligiendo empresa) y cuentas mensuales entre Distriplus y Logipaper.

Reglas de Juan:
- Todo el material entra y sale del Almacén Central (DISTRIPLUS). Los albaranes de cliente se facturan por la empresa que toque
  (por defecto, la de la última factura a ese cliente). Se usa el asistente de Nextdoo ("Facturación Albaranes"), que enlaza la
  factura al pedido aunque sea de otra empresa.
- Cada mes: Distriplus factura a Logipaper el material que Logipaper vendió del almacén central, y Logipaper factura a Distriplus
  el material que Logipaper compró a proveedores y entró en el almacén central. Precio: última compra + 5 %. Una línea genérica
  "Venta general mes … según PDF adjunto" y el PDF con todas las transacciones. Lo que Logipaper y Distriplus se venden entre ellas
  como cliente (Tienda 3, etc.) va aparte y no entra aquí.
"""
import base64
import calendar
import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

from fastapi import Request, HTTPException
from fastapi.responses import Response

DISTRI, LOGI = 1, 4
TODAS = [1, 2, 3, 4, 5]
MARGEN = 0.05
MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def pdf_texto(titulo, lineas):
    """PDF mínimo (Courier, A4 apaisado) sin librerías: lo justo para un listado."""
    def esc(t):
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    por_pag = 48
    paginas = [lineas[i:i + por_pag] for i in range(0, max(len(lineas), 1), por_pag)] or [[]]
    objs = []

    def add(o):
        objs.append(o)
        return len(objs)
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    fontb = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold /Encoding /WinAnsiEncoding >>")
    pages_id = len(objs) + 1 + 2 * len(paginas)  # se reserva después
    kids = []
    for n, pag in enumerate(paginas, 1):
        s = ["BT", "/F2 11 Tf", "36 560 Td", f"({esc(titulo)}) Tj", "/F1 7.6 Tf", "0 -16 Td", "9.4 TL"]
        for l in pag:
            s.append(f"({esc(l)}) '")
        s += ["ET", "BT", "/F1 7 Tf", "760 20 Td", f"(Pag. {n}/{len(paginas)}) Tj", "ET"]
        data = "\n".join(s).encode("cp1252", "replace")
        cont = add(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 842 595] /Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> /Contents %d 0 R >>"
                        % (pages_id, font, fontb, cont)))
    assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(f"{k} 0 R" for k in kids).encode(), len(kids))) == pages_id
    cat = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    out, ofs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        ofs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in ofs)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, cat, x)
    return bytes(out)


def setup(app, *, data_dir: Path, odoo, current_user, scope, guard):
    db_path = data_dir / "compras.db"
    lock = threading.Lock()

    def con():
        c = sqlite3.connect(db_path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE IF NOT EXISTS liquidaciones (mes TEXT, sentido TEXT, importe REAL, venta_id INTEGER, compra_id INTEGER, usuario TEXT, cuando REAL, PRIMARY KEY (mes, sentido))")
        return c

    def ctx(request, company="all"):
        u = current_user(request)
        cids, _ = scope(u, company)
        return u, cids

    def todas(request):
        u, cids = ctx(request)
        if DISTRI not in cids or LOGI not in cids:
            raise HTTPException(403, "Hace falta ver Distriplus y Logipaper")
        return u, cids

    def eur(x):
        return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    # ---------------- Facturar albaranes ----------------
    @app.get("/api/facturar/albaranes")
    def albaranes(request: Request):
        u, cids = ctx(request)

        def run():
            F = ["name", "partner_id", "date_done", "origin", "sale_id", "company_id", "location_id", "move_ids", "invoice_state"]
            base = [("picking_type_code", "=", "outgoing"), ("state", "=", "done"), ("sale_id", "!=", False)]
            try:
                pk = odoo.search_read("stock.picking", base + [("albaran_invoiced", "=", False)], F, company_ids=cids, order="date_done asc", limit=800)
            except Exception:
                desde = (dt.date.today() - dt.timedelta(days=180)).isoformat()
                pk = odoo.search_read("stock.picking", base + [("date_done", ">=", desde)], F, company_ids=cids, order="date_done asc", limit=3000)
            pk = [p for p in pk if p.get("invoice_state") == "pending"]
            mids = [m for p in pk for m in p["move_ids"]]
            mv = odoo.search_read("stock.move", [("id", "in", mids), ("state", "=", "done")], ["picking_id", "quantity", "sale_line_id"], company_ids=cids) if mids else []
            slids = [m["sale_line_id"][0] for m in mv if m.get("sale_line_id")]
            sl = {l["id"]: l for l in odoo.search_read("sale.order.line", [("id", "in", slids)], ["price_unit", "discount"], company_ids=cids)} if slids else {}
            imp = {}
            for m in mv:
                l = sl.get((m.get("sale_line_id") or [0])[0])
                if l:
                    imp[m["picking_id"][0]] = imp.get(m["picking_id"][0], 0) + m["quantity"] * l["price_unit"] * (1 - (l.get("discount") or 0) / 100)
            partners = odoo.search_read("res.partner", [("id", "in", list({p["partner_id"][0] for p in pk if p.get("partner_id")}))], ["commercial_partner_id"], company_ids=cids) if pk else []
            comm = {p["id"]: p["commercial_partner_id"][0] for p in partners}
            ultimas = {}
            if comm:
                for f in odoo.search_read("account.move", [("move_type", "=", "out_invoice"), ("state", "=", "posted"), ("commercial_partner_id", "in", list(set(comm.values())))],
                                          ["commercial_partner_id", "company_id", "invoice_date", "name"], company_ids=TODAS, order="invoice_date desc, id desc", limit=3000):
                    ultimas.setdefault(f["commercial_partner_id"][0], f)
            emp = {c["id"]: c["name"] for c in odoo.search_read("res.company", [("id", "in", cids)], ["name"], company_ids=cids)}
            grupos = {}
            for p in pk:
                cp = comm.get((p.get("partner_id") or [0])[0], (p.get("partner_id") or [0])[0])
                g = grupos.setdefault(cp, {"partner_id": cp, "cliente": (p.get("partner_id") or [0, "—"])[1].split(",")[0], "albaranes": [], "total": 0})
                g["albaranes"].append({"id": p["id"], "ref": p["name"], "pedido": (p.get("sale_id") or [0, ""])[1], "fecha": (p.get("date_done") or "")[:10],
                                       "empresa_albaran": p["company_id"][0], "almacen": p["location_id"][1], "importe": round(imp.get(p["id"], 0), 2)})
                g["total"] += imp.get(p["id"], 0)
            for cp, g in grupos.items():
                u_f = ultimas.get(cp)
                ea = g["albaranes"][0]["empresa_albaran"]
                # albaranes del almacén central: Distriplus o Logipaper según la última factura; los de otra empresa (Ingraf) se facturan por ella
                g["empresa"] = u_f["company_id"][0] if (u_f and ea == DISTRI and u_f["company_id"][0] in (DISTRI, LOGI)) else ea
                g["ultima"] = f"{u_f['name']} ({u_f['company_id'][1].split()[0]}, {u_f['invoice_date']})" if u_f else ""
                g["total"] = round(g["total"], 2)
            return {"empresas": [{"id": k, "nombre": v} for k, v in emp.items()], "grupos": sorted(grupos.values(), key=lambda g: g["cliente"].lower())}
        return guard(run)

    @app.post("/api/facturar/albaranes")
    async def facturar(request: Request):
        u, cids = ctx(request)
        b = await request.json()
        directo = b.get("modo") == "directo"
        fecha = b.get("fecha") or ""
        if fecha:
            try:
                dt.date.fromisoformat(fecha)
            except ValueError:
                raise HTTPException(400, "Fecha no válida")
        por_emp = {}
        for g in b.get("grupos") or []:
            if int(g["empresa"]) not in cids:
                raise HTTPException(403, "Empresa no permitida")
            por_emp.setdefault(int(g["empresa"]), []).extend(int(i) for i in g["picking_ids"])
        if not por_emp:
            raise HTTPException(400, "No hay albaranes marcados")

        def run():
            hechos = []
            for emp, ids in por_emp.items():
                jr = odoo.search_read("account.journal", [("type", "=", "sale"), ("company_id", "=", emp)], ["name"], company_ids=[emp], order="sequence, id", limit=1)
                ctx_w = {"active_model": "stock.picking", "active_ids": ids, "allowed_company_ids": [emp] + [c for c in TODAS if c != emp]}
                wid = odoo.call("stock.picking.invoice.wizard", "create", TODAS, vals_list=[{"picking_ids": [[6, 0, ids]], "company_id": emp, "journal_id": jr[0]["id"]}], context=ctx_w)
                wid = wid[0] if isinstance(wid, list) else wid
                # siempre en borrador primero, para poder poner la fecha elegida; luego se contabiliza si se pidió "directo"
                res = odoo.call("stock.picking.invoice.wizard", "action_create_draft", TODAS, ids=[wid], context=ctx_w) or {}
                inv = [res["res_id"]] if res.get("res_id") else next((d[2] for d in res.get("domain") or [] if isinstance(d, (list, tuple)) and d[0] == "id"), [])
                errores = []
                if inv and fecha:
                    odoo.call("account.move", "write", TODAS, ids=inv, vals={"invoice_date": fecha, "date": fecha})
                if inv and directo:
                    for i in inv:
                        try:
                            odoo.call("account.move", "action_post", TODAS, ids=[i])
                        except Exception as e:
                            errores.append(str(e)[:200])
                fs = odoo.search_read("account.move", [("id", "in", inv)], ["name", "amount_total", "partner_id", "state", "invoice_date"], company_ids=TODAS) if inv else []
                hechos.append({"empresa": emp, "albaranes": len(ids), "errores": errores,
                               "facturas": [{"id": f["id"], "nombre": f["name"] if f["state"] == "posted" else "borrador", "cliente": f["partner_id"][1],
                                             "total": f["amount_total"], "estado": f["state"], "fecha": f.get("invoice_date")} for f in fs]})
            return {"hechos": hechos}
        return guard(run)

    @app.get("/api/facturar/sin_enviar")
    def sin_enviar(request: Request, dias: int = 60):
        """Facturas de cliente contabilizadas que todavía no se han enviado por email."""
        u, cids = ctx(request)

        def run():
            desde = (dt.date.today() - dt.timedelta(days=dias)).isoformat()
            fs = odoo.search_read("account.move", [("move_type", "in", ["out_invoice", "out_refund"]), ("state", "=", "posted"), ("is_move_sent", "=", False),
                                                   ("invoice_date", ">=", desde), ("journal_id.name", "not ilike", "TPV"), ("pos_order_ids", "=", False)],
                                  ["name", "partner_id", "invoice_date", "amount_total", "company_id", "move_type"], company_ids=cids, order="invoice_date desc, id desc", limit=300)
            pids = list({f["partner_id"][0] for f in fs})
            em = {p["id"]: p.get("email") or "" for p in odoo.search_read("res.partner", [("id", "in", pids)], ["email"], company_ids=cids)} if pids else {}
            return [{"id": f["id"], "nombre": f["name"], "cliente": f["partner_id"][1], "email": em.get(f["partner_id"][0], ""), "fecha": f["invoice_date"],
                     "total": f["amount_total"], "empresa": f["company_id"][1].split()[0], "abono": f["move_type"] == "out_refund"} for f in fs]
        return guard(run)

    @app.post("/api/facturar/enviar")
    async def enviar(request: Request):
        """Envía por email cada factura a su cliente con la plantilla de factura de Odoo (con el PDF) y la marca como enviada."""
        u, cids = ctx(request)
        b = await request.json()
        ids = [int(i) for i in b.get("ids") or []]
        if not ids:
            raise HTTPException(400, "No hay facturas marcadas")

        def run():
            tpl = odoo.search_read("mail.template", [("model", "=", "account.move"), "|", ("name", "ilike", "factura"), ("name", "ilike", "invoice")],
                                   ["name"], company_ids=cids, order="id", limit=5)
            if not tpl:
                raise HTTPException(400, "No encuentro la plantilla de email de facturas en Odoo")
            tid = next((t["id"] for t in tpl if "envío" in t["name"].lower() or "sending" in t["name"].lower()), tpl[0]["id"])
            fs = odoo.search_read("account.move", [("id", "in", ids), ("state", "=", "posted")], ["name", "partner_id", "company_id"], company_ids=TODAS)
            ok, mal = [], []
            for f in fs:
                p = odoo.search_read("res.partner", [("id", "=", f["partner_id"][0])], ["email"], company_ids=TODAS)
                if not p or not p[0].get("email"):
                    mal.append(f"{f['name']}: el cliente no tiene email"); continue
                try:
                    c = f["company_id"][0]
                    odoo.call("mail.template", "send_mail", [c] + [x for x in TODAS if x != c], ids=[tid], res_id=f["id"], force_send=True)
                    odoo.call("account.move", "write", [c] + [x for x in TODAS if x != c], ids=[f["id"]], vals={"is_move_sent": True})
                    ok.append(f["name"])
                except Exception as e:
                    mal.append(f"{f['name']}: {str(e)[:150]}")
            return {"enviadas": ok, "errores": mal}
        return guard(run)

    # ---------------- Cuentas entre empresas ----------------
    def rango(mes):
        y, m = map(int, mes.split("-"))
        return dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])

    def ultima_compra(pids, hasta):
        """Precio neto de la última compra de cada artículo hasta esa fecha (cualquier empresa); si no hay, coste de Distriplus."""
        out = {}
        # sin las compras entre nuestras propias empresas (no son precio de proveedor)
        propios = [c["partner_id"][0] for c in odoo.search_read("res.company", [], ["partner_id"], company_ids=TODAS)]
        for i in range(0, len(pids), 200):
            trozo = pids[i:i + 200]
            for l in odoo.search_read("purchase.order.line", [("product_id", "in", trozo), ("order_id.state", "in", ["purchase", "done"]),
                                                              ("order_id.partner_id", "not in", propios),
                                                              ("order_id.date_order", "<=", hasta.isoformat() + " 23:59:59"), ("price_unit", ">", 0)],
                                      ["product_id", "price_unit", "discount", "order_id"], company_ids=TODAS, order="id desc"):
                out.setdefault(l["product_id"][0], (round(l["price_unit"] * (1 - (l.get("discount") or 0) / 100), 4), l["order_id"][1]))
            falta = [p for p in trozo if p not in out]
            if falta:
                for p in odoo.search_read("product.product", [("id", "in", falta)], ["standard_price"], company_ids=[DISTRI]):
                    out[p["id"]] = (p["standard_price"], "coste Odoo")
        return out

    def calcular(mes):
        ini, fin = rango(mes)
        # A) Lo que vendió Logipaper con albaranes del almacén central (pedidos de Distriplus)
        fac = odoo.search_read("account.move", [("company_id", "=", LOGI), ("move_type", "in", ["out_invoice", "out_refund"]), ("state", "=", "posted"),
                                                ("invoice_date", ">=", ini.isoformat()), ("invoice_date", "<=", fin.isoformat())],
                               ["name", "invoice_date", "partner_id", "move_type"], company_ids=TODAS)
        fmap = {f["id"]: f for f in fac}
        lin = odoo.search_read("account.move.line", [("move_id", "in", list(fmap)), ("display_type", "=", "product"), ("sale_line_ids", "!=", False)],
                               ["move_id", "product_id", "quantity", "sale_line_ids", "price_subtotal"], company_ids=TODAS) if fmap else []
        sids = list({s for l in lin for s in l["sale_line_ids"]})
        sl = {s["id"]: s for s in odoo.search_read("sale.order.line", [("id", "in", sids)], ["company_id", "order_id"], company_ids=TODAS)} if sids else {}
        lin = [l for l in lin if any(sl.get(s, {}).get("company_id", [0])[0] == DISTRI for s in l["sale_line_ids"]) and l.get("product_id")]
        precios = ultima_compra(list({l["product_id"][0] for l in lin}), fin)
        ventas = []
        for l in lin:
            f = fmap[l["move_id"][0]]
            signo = -1 if f["move_type"] == "out_refund" else 1
            coste, origen = precios.get(l["product_id"][0], (0, ""))
            q = signo * l["quantity"]
            ventas.append({"fecha": f["invoice_date"], "factura": f["name"], "cliente": f["partner_id"][1], "producto": l["product_id"][1], "cantidad": q,
                           "coste": coste, "origen_coste": origen, "importe": round(q * coste * (1 + MARGEN), 2),
                           "pedido": sl.get(l["sale_line_ids"][0], {}).get("order_id", [0, ""])[1]})
        # B) Lo que compró Logipaper a proveedores y entró en el almacén central (facturas cruzadas con pedidos de Distriplus)
        with con() as c:
            marcas = {r["move_id"]: json.loads(r["nota"] or "{}") for r in c.execute("SELECT move_id, nota FROM factura_marcas WHERE tipo='pedido'").fetchall()}
        compras = []
        if marcas:
            for f in odoo.search_read("account.move", [("id", "in", list(marcas)), ("company_id", "=", LOGI), ("state", "=", "posted"),
                                                       ("invoice_date", ">=", ini.isoformat()), ("invoice_date", "<=", fin.isoformat())],
                                      ["name", "ref", "invoice_date", "partner_id", "amount_untaxed_signed", "move_type"], company_ids=TODAS):
                if marcas[f["id"]].get("empresa_pedido", DISTRI) != DISTRI:
                    continue
                base = -f["amount_untaxed_signed"]  # en facturas de proveedor el signo va en negativo
                compras.append({"fecha": f["invoice_date"], "factura": f"{f['name']} ({f.get('ref') or ''})", "proveedor": f["partner_id"][1],
                                "pedido": marcas[f["id"]].get("pedido", ""), "base": round(base, 2), "importe": round(base * (1 + MARGEN), 2)})
        ta, tb = round(sum(v["importe"] for v in ventas), 2), round(sum(x["importe"] for x in compras), 2)
        with con() as c:
            hechas = {r["sentido"]: dict(r) for r in c.execute("SELECT * FROM liquidaciones WHERE mes=?", (mes,)).fetchall()}
        return {"mes": mes, "texto_mes": f"{MESES[ini.month]} {ini.year}", "ventas": ventas, "compras": compras,
                "distri_a_logi": ta, "logi_a_distri": tb, "hechas": hechas, "margen": MARGEN}

    def pdf_de(d, sentido):
        if sentido == "distri_a_logi":
            t = f"DISTRIPLUS -> LOGIPAPER · Material del almacen central vendido por Logipaper · {d['texto_mes']} (ultima compra + {int(MARGEN*100)}%)"
            ls = [f"{'Fecha':10} {'Factura':18} {'Cliente':28} {'Articulo':52} {'Cant':>7} {'Ult.compra':>10} {'Importe':>11}", "-" * 142]
            for v in sorted(d["ventas"], key=lambda v: (v["fecha"], v["factura"])):
                ls.append(f"{v['fecha']:10} {v['factura'][:18]:18} {v['cliente'][:28]:28} {v['producto'][:52]:52} {v['cantidad']:>7g} {eur(v['coste']):>10} {eur(v['importe']):>11}")
            ls += ["-" * 142, f"{'TOTAL (base imponible, sin IVA)':>120} {eur(d['distri_a_logi']):>21}"]
        else:
            t = f"LOGIPAPER -> DISTRIPLUS · Compras de Logipaper entradas en el almacen central · {d['texto_mes']} (coste + {int(MARGEN*100)}%)"
            ls = [f"{'Fecha':10} {'Factura proveedor':34} {'Proveedor':36} {'Pedido':10} {'Base':>12} {'Importe':>12}", "-" * 120]
            for x in sorted(d["compras"], key=lambda x: x["fecha"]):
                ls.append(f"{x['fecha']:10} {x['factura'][:34]:34} {x['proveedor'][:36]:36} {x['pedido'][:10]:10} {eur(x['base']):>12} {eur(x['importe']):>12}")
            ls += ["-" * 120, f"{'TOTAL (base imponible, sin IVA)':>96} {eur(d['logi_a_distri']):>23}"]
        return pdf_texto(t, ls)

    @app.get("/api/entre_empresas")
    def liquidacion(request: Request, mes: str = ""):
        todas(request)
        mes = mes or (dt.date.today().replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
        return guard(lambda: calcular(mes))

    @app.get("/api/entre_empresas/pdf")
    def liquidacion_pdf(request: Request, mes: str, sentido: str):
        todas(request)
        d = guard(lambda: calcular(mes))
        return Response(pdf_de(d, sentido), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="liquidacion_{sentido}_{mes}.pdf"'})

    @app.post("/api/entre_empresas/crear")
    async def crear(request: Request):
        """Crea en borrador la factura de venta de una empresa a la otra y la de compra en la otra, con el PDF del detalle."""
        u, _ = todas(request)
        b = await request.json()
        mes, sentido = b.get("mes"), b.get("sentido")
        if sentido not in ("distri_a_logi", "logi_a_distri"):
            raise HTTPException(400, "Sentido no válido")

        def run():
            d = calcular(mes)
            if sentido in d["hechas"]:
                raise HTTPException(400, "Esa liquidación ya está creada")
            importe = d[sentido]
            if importe <= 0:
                raise HTTPException(400, "No hay importe que facturar en ese sentido")
            vende, compra = (DISTRI, LOGI) if sentido == "distri_a_logi" else (LOGI, DISTRI)
            cs = {c["id"]: c for c in odoo.search_read("res.company", [("id", "in", [DISTRI, LOGI])], ["partner_id", "name"], company_ids=TODAS)}
            texto = f"Venta general mes de {d['texto_mes']} según PDF adjunto"
            pdf = base64.b64encode(pdf_de(d, sentido)).decode()

            def impuesto(cid, uso):
                ts = odoo.search_read("account.tax", [("type_tax_use", "=", uso), ("amount", "=", 21), ("company_id", "=", cid), ("amount_type", "=", "percent")],
                                      ["name"], company_ids=[cid], order="sequence, id")
                import re
                pref = [t for t in ts if re.search(r"\bG\b|bienes", t["name"], re.I)] or ts
                return [pref[0]["id"]] if pref else []

            def factura(cid, tipo, partner, uso):
                jr = odoo.search_read("account.journal", [("type", "=", "sale" if tipo == "out_invoice" else "purchase"), ("company_id", "=", cid)], ["id"], company_ids=[cid], order="sequence, id", limit=1)
                vals = {"move_type": tipo, "journal_id": jr[0]["id"], "partner_id": partner, "invoice_date": rango(mes)[1].isoformat(),
                        "invoice_line_ids": [[0, 0, {"name": texto, "quantity": 1, "price_unit": importe, "tax_ids": [[6, 0, impuesto(cid, uso)]]}]]}
                if tipo == "in_invoice":
                    vals["ref"] = f"Liquidación {mes}"
                mid = odoo.call("account.move", "create", [cid], vals_list=[vals], context={"default_move_type": tipo})
                mid = mid[0] if isinstance(mid, list) else mid
                odoo.call("ir.attachment", "create", [cid], vals_list=[{"name": f"Liquidacion_{mes}_{sentido}.pdf", "res_model": "account.move", "res_id": mid,
                                                                      "mimetype": "application/pdf", "datas": pdf}])
                return mid
            v = factura(vende, "out_invoice", cs[compra]["partner_id"][0], "sale")
            c_ = factura(compra, "in_invoice", cs[vende]["partner_id"][0], "purchase")
            import time
            with lock, con() as c:
                c.execute("INSERT INTO liquidaciones VALUES (?,?,?,?,?,?,?)", (mes, sentido, importe, v, c_, u, time.time()))
            return {"ok": True, "venta_id": v, "compra_id": c_, "importe": importe}
        return guard(run)
