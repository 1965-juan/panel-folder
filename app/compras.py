"""Compras: subir albaranes y facturas de proveedor (PDF), recibir albaranes contra el pedido de compra,
revisión de facturas en borrador para Mayte y lista de facturas recurrentes del mes.

- Las facturas se suben a Odoo como borrador y Odoo las lee solo (OCR activado en todas las empresas).
- Los albaranes se guardan en el panel y se enlazan con la recepción del pedido de compra; al recibirlos,
  el PDF queda adjunto a la recepción en Odoo.
"""
import base64
import datetime as dt
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import Request, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse


def setup(app, *, data_dir: Path, odoo, current_user, scope, guard, es_admin, demo):
    carpeta = data_dir / "compras"
    carpeta.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "compras.db"
    lock = threading.Lock()

    def con():
        c = sqlite3.connect(db_path, timeout=20)
        c.row_factory = sqlite3.Row
        c.executescript("""
        CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, tipo TEXT, nombre TEXT, fichero TEXT, empresa INTEGER,
            subido REAL, usuario TEXT, estado TEXT, odoo_model TEXT, odoo_id INTEGER, nota TEXT);
        CREATE TABLE IF NOT EXISTS recurrentes (id INTEGER PRIMARY KEY AUTOINCREMENT, empresa INTEGER, empresa_nombre TEXT,
            partner_id INTEGER, partner_nombre TEXT, concepto TEXT, importe REAL, dia INTEGER, activo INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS recurrentes_marcas (rid INTEGER, mes TEXT, nota TEXT, PRIMARY KEY (rid, mes));
        """)
        return c

    def ctx(request, company="all"):
        u = current_user(request)
        cids, _ = scope(u, company)
        return u, cids

    def una_empresa(request, company):
        u, cids = ctx(request, company)
        if company in ("", "all", None):
            return u, cids[0], cids
        return u, int(company), cids

    # ---------- subir PDF ----------
    @app.post("/api/compras/subir")
    async def subir(request: Request, tipo: str = Form(...), company: str = Form("all"), archivos: list[UploadFile] = File(...)):
        u, cid, cids = una_empresa(request, company)
        if tipo not in ("albaran", "factura"):
            raise HTTPException(400, "Tipo no válido")
        out = []
        for f in archivos:
            datos = await f.read()
            if not datos:
                continue
            if not (f.filename or "").lower().endswith(".pdf") and not datos.startswith(b"%PDF"):
                out.append({"nombre": f.filename, "error": "No es un PDF"})
                continue
            did = secrets.token_hex(8)
            fich = carpeta / f"{did}.pdf"
            fich.write_bytes(datos)
            item = {"id": did, "nombre": f.filename, "tipo": tipo}
            estado, model, oid = "pendiente", None, None
            if tipo == "factura" and not demo:
                try:
                    jr = odoo.search_read("account.journal", [("type", "=", "purchase"), ("company_id", "=", cid)], ["id"], company_ids=[cid], limit=1)
                    att = odoo.call("ir.attachment", "create", [cid], vals_list=[{
                        "name": f.filename or "factura.pdf", "datas": base64.b64encode(datos).decode(), "mimetype": "application/pdf"}])
                    att = att[0] if isinstance(att, list) else att
                    act = odoo.call("account.journal", "create_document_from_attachment", [cid], ids=[jr[0]["id"]], attachment_ids=[att])
                    oid = (act or {}).get("res_id")
                    if not oid:
                        dom = (act or {}).get("domain") or []
                        ids = next((d[2] for d in dom if isinstance(d, (list, tuple)) and d[0] == "id"), [])
                        oid = ids[0] if ids else None
                    model, estado = "account.move", "en_odoo"
                except Exception as e:
                    estado = "error"
                    item["error"] = f"No se pudo subir a Odoo: {e}"[:300]
            elif tipo == "factura" and demo:
                model, oid, estado = "account.move", 9001, "en_odoo"
            with lock, con() as c:
                c.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (did, tipo, f.filename, fich.name, cid, time.time(), u, estado, model, oid, item.get("error")))
            item.update(estado=estado, odoo_id=oid)
            out.append(item)
        return out

    @app.get("/api/compras/docs")
    def docs(request: Request, tipo: str = "", estado: str = "", company: str = "all"):
        _, cids = ctx(request, company)
        q = "SELECT * FROM docs WHERE empresa IN (%s)" % ",".join("?" * len(cids))
        args = list(cids)
        if tipo:
            q += " AND tipo=?"; args.append(tipo)
        if estado:
            q += " AND estado=?"; args.append(estado)
        with con() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY subido DESC LIMIT 200", args).fetchall()]

    @app.get("/api/compras/pdf/{did}")
    def pdf(did: str, request: Request):
        current_user(request)
        with con() as c:
            r = c.execute("SELECT fichero, nombre FROM docs WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404)
        return FileResponse(carpeta / r["fichero"], media_type="application/pdf", filename=r["nombre"],
                            content_disposition_type="inline")

    @app.delete("/api/compras/docs/{did}")
    def borrar_doc(did: str, request: Request):
        current_user(request)
        with lock, con() as c:
            c.execute("UPDATE docs SET estado='descartado' WHERE id=?", (did,))
        return {"ok": True}

    # ---------- recepciones (albaranes) ----------
    @app.get("/api/compras/recepciones")
    def recepciones(request: Request, company: str = "all"):
        _, cids = ctx(request, company)

        def run():
            pk = odoo.search_read("stock.picking", [("picking_type_code", "=", "incoming"), ("state", "in", ["assigned", "confirmed", "waiting"])],
                                  ["name", "partner_id", "origin", "scheduled_date", "backorder_id", "company_id", "purchase_id", "move_ids"],
                                  company_ids=cids, order="scheduled_date asc", limit=200)
            mids = [m for p in pk for m in p.get("move_ids", [])]
            mv = odoo.search_read("stock.move", [("id", "in", mids)], ["picking_id", "product_id", "product_uom_qty", "quantity", "purchase_line_id"],
                                  company_ids=cids) if mids else []
            lines = {}
            for m in mv:
                lines.setdefault(m["picking_id"][0], []).append({"move_id": m["id"], "producto": m["product_id"][1], "product_id": m["product_id"][0],
                                                                 "pedido": m["product_uom_qty"]})
            hoy = dt.date.today()
            out = []
            for p in pk:
                fecha = (p.get("scheduled_date") or "")[:10]
                dias = (hoy - dt.date.fromisoformat(fecha)).days if fecha else 0
                out.append({"id": p["id"], "ref": p["name"], "proveedor": (p.get("partner_id") or [0, "—"])[1],
                            "partner_id": (p.get("partner_id") or [0])[0], "origen": p.get("origin") or "",
                            "pedido": (p.get("purchase_id") or [0, ""])[1], "fecha": fecha, "dias": dias,
                            "pendiente_de": (p.get("backorder_id") or [0, ""])[1], "empresa": p["company_id"][1],
                            "lineas": lines.get(p["id"], [])})
            return out
        return guard(run)

    @app.post("/api/compras/recibir")
    async def recibir(request: Request, company: str = "all"):
        _, cids = ctx(request, company)
        b = await request.json()
        pid = int(b["picking_id"])
        cant = {int(k): float(v or 0) for k, v in (b.get("cantidades") or {}).items()}

        def run():
            pk = odoo.search_read("stock.picking", [("id", "=", pid)], ["company_id", "name", "origin", "move_ids"], company_ids=cids)
            if not pk:
                raise HTTPException(404, "Recepción no encontrada")
            pk = pk[0]
            c1 = [pk["company_id"][0]]
            if not any(v > 0 for v in cant.values()):
                raise HTTPException(400, "No has puesto ninguna cantidad recibida")
            for mid in pk["move_ids"]:
                q = cant.get(mid, 0)
                odoo.call("stock.move", "write", c1, ids=[mid], vals={"quantity": q, "picked": q > 0})
            if b.get("albaran"):
                odoo.call("stock.picking", "write", c1, ids=[pid], vals={"origin": f"{pk.get('origin') or ''} · Albarán {b['albaran']}".strip(" ·")})
            res = odoo.call("stock.picking", "button_validate", c1, ids=[pid])
            pendiente = False
            if isinstance(res, dict) and res.get("res_model") == "stock.backorder.confirmation":
                wctx = dict(res.get("context") or {}, allowed_company_ids=c1)
                w = odoo.call("stock.backorder.confirmation", "create", None, vals_list=[{}], context=wctx)
                w = w[0] if isinstance(w, list) else w
                odoo.call("stock.backorder.confirmation", "process", None, ids=[w], context=wctx)
                pendiente = True
            elif isinstance(res, dict) and res.get("res_model"):
                raise HTTPException(400, f"Odoo pide confirmar algo más ({res.get('name')}). Termínalo en Odoo.")
            # adjuntar el PDF del albarán a la recepción
            if b.get("doc_id"):
                with con() as c:
                    r = c.execute("SELECT fichero, nombre FROM docs WHERE id=?", (b["doc_id"],)).fetchone()
                if r:
                    odoo.call("ir.attachment", "create", c1, vals_list=[{
                        "name": r["nombre"] or "albaran.pdf", "res_model": "stock.picking", "res_id": pid, "mimetype": "application/pdf",
                        "datas": base64.b64encode((carpeta / r["fichero"]).read_bytes()).decode()}])
                    with lock, con() as c:
                        c.execute("UPDATE docs SET estado='recibido', odoo_model='stock.picking', odoo_id=? WHERE id=?", (pid, b["doc_id"]))
            # qué se puede servir ahora con lo que ha entrado
            prods = {m["product_id"][0] for m in odoo.search_read("stock.move", [("id", "in", pk["move_ids"])], ["product_id"], company_ids=c1)
                     if cant.get(m["id"], 0) > 0}
            servir = []
            try:
                d = odoo.call_kw("sale.order", "get_panel_data", [], company_ids=cids)
                for o in d.get("orders", []):
                    for l in o["lines"]:
                        if l["product_id"] in prods:
                            servir.append({"quien": o["partner_name"], "ref": o["order_name"], "producto": l["product_name"], "pendiente": l["qty_pending"]})
                for p in d.get("reposicion", []):
                    for l in p["lines"]:
                        if l["product_id"] in prods:
                            servir.append({"quien": p["store_name"], "ref": p["picking_name"], "producto": l["product_name"], "pendiente": l["qty_pending"]})
            except Exception:
                pass
            return {"ok": True, "queda_pendiente": pendiente, "servir": servir}
        return guard(run)

    # ---------- facturas para Mayte ----------
    @app.get("/api/compras/facturas")
    def facturas(request: Request, company: str = "all"):
        _, cids = ctx(request, company)

        def run():
            mv = odoo.search_read("account.move", [("move_type", "in", ["in_invoice", "in_refund"]), ("state", "=", "draft")],
                                  ["partner_id", "ref", "invoice_date", "invoice_date_due", "amount_untaxed", "amount_total", "company_id",
                                   "invoice_origin", "extract_state", "create_date", "move_type", "invoice_line_ids"],
                                  company_ids=cids, order="create_date desc", limit=150)
            lids = [l for m in mv for l in m.get("invoice_line_ids", [])]
            ln = odoo.search_read("account.move.line", [("id", "in", lids), ("display_type", "=", "product")],
                                  ["move_id", "product_id", "name", "quantity", "price_unit", "price_subtotal", "purchase_line_id"],
                                  company_ids=cids) if lids else []
            pls = [l["purchase_line_id"][0] for l in ln if l.get("purchase_line_id")]
            pol = {p["id"]: p for p in odoo.search_read("purchase.order.line", [("id", "in", pls)],
                                                        ["order_id", "product_qty", "qty_received", "qty_invoiced", "price_unit"], company_ids=cids)} if pls else {}
            por_factura = {}
            for l in ln:
                por_factura.setdefault(l["move_id"][0], []).append(l)
            # adjuntos (para ver el PDF en Odoo)
            atts = odoo.search_read("ir.attachment", [("res_model", "=", "account.move"), ("res_id", "in", [m["id"] for m in mv]),
                                                      ("mimetype", "=", "application/pdf")], ["res_id"], company_ids=cids) if mv else []
            con_pdf = {a["res_id"]: a["id"] for a in atts}
            out = []
            for m in mv:
                lineas, avisos, pedidos = [], [], set()
                for l in por_factura.get(m["id"], []):
                    item = {"producto": (l.get("product_id") or [0, l.get("name") or ""])[1], "cantidad": l["quantity"],
                            "precio": l["price_unit"], "importe": l["price_subtotal"]}
                    p = pol.get((l.get("purchase_line_id") or [0])[0])
                    if p:
                        pedidos.add(p["order_id"][1])
                        item["recibido"] = p["qty_received"]
                        item["pedido"] = p["product_qty"]
                        item["precio_pedido"] = p["price_unit"]
                        if l["quantity"] > p["qty_received"] + 1e-6:
                            avisos.append(f"{item['producto'][:40]}: se facturan {l['quantity']:g} y han llegado {p['qty_received']:g}")
                        if abs(l["price_unit"] - p["price_unit"]) > 0.005:
                            avisos.append(f"{item['producto'][:40]}: precio {l['price_unit']:.2f} y en el pedido {p['price_unit']:.2f}")
                    lineas.append(item)
                faltan = []
                if not m.get("partner_id"):
                    faltan.append("proveedor")
                if not m.get("invoice_date"):
                    faltan.append("fecha de factura")
                if not m.get("ref"):
                    faltan.append("nº de factura")
                leyendo = m.get("extract_state") in ("waiting_extraction", "extract_not_ready")
                estado = "leyendo" if leyendo else ("diferencias" if avisos or faltan else "ok")
                out.append({"id": m["id"], "proveedor": (m.get("partner_id") or [0, "Sin proveedor"])[1], "numero": m.get("ref") or "",
                            "fecha": m.get("invoice_date") or "", "vence": m.get("invoice_date_due") or "",
                            "base": m["amount_untaxed"], "total": m["amount_total"], "empresa": m["company_id"][1],
                            "abono": m["move_type"] == "in_refund", "pedidos": sorted(pedidos) or ([m["invoice_origin"]] if m.get("invoice_origin") else []),
                            "lineas": lineas, "avisos": avisos, "faltan": faltan, "estado": estado, "pdf": con_pdf.get(m["id"]),
                            "sin_pedido": not pedidos})
            return out
        return guard(run)

    @app.post("/api/compras/facturas/{mid}/ok")
    def factura_ok(mid: int, request: Request, company: str = "all"):
        _, cids = ctx(request, company)

        def run():
            m = odoo.search_read("account.move", [("id", "=", mid), ("state", "=", "draft")], ["company_id", "partner_id", "invoice_date"], company_ids=cids)
            if not m:
                raise HTTPException(404, "La factura ya no está en borrador")
            if not m[0].get("partner_id") or not m[0].get("invoice_date"):
                raise HTTPException(400, "Falta el proveedor o la fecha: complétalo en Odoo antes de darle el OK")
            odoo.call("account.move", "action_post", [m[0]["company_id"][0]], ids=[mid])
            with lock, con() as c:
                c.execute("UPDATE docs SET estado='contabilizada' WHERE odoo_model='account.move' AND odoo_id=?", (mid,))
            return {"ok": True}
        return guard(run)

    # ---------- facturas recurrentes ----------
    @app.get("/api/compras/recurrentes")
    def recurrentes(request: Request, mes: str = "", company: str = "all"):
        _, cids = ctx(request, company)
        mes = mes or dt.date.today().strftime("%Y-%m")
        y, m = map(int, mes.split("-"))
        ini = dt.date(y, m, 1)
        fin = (ini + dt.timedelta(days=32)).replace(day=1)
        with con() as c:
            rs = [dict(r) for r in c.execute("SELECT * FROM recurrentes WHERE activo=1 AND empresa IN (%s) ORDER BY dia, partner_nombre"
                                              % ",".join("?" * len(cids)), cids).fetchall()]
            marcas = {r["rid"]: r["nota"] for r in c.execute("SELECT * FROM recurrentes_marcas WHERE mes=?", (mes,)).fetchall()}

        def run():
            pids = list({r["partner_id"] for r in rs if r["partner_id"]})
            mv = odoo.search_read("account.move", [("move_type", "=", "in_invoice"), ("state", "!=", "cancel"), ("partner_id", "in", pids),
                                                   "|", "&", ("invoice_date", ">=", ini.isoformat()), ("invoice_date", "<", fin.isoformat()),
                                                   "&", ("invoice_date", "=", False), ("create_date", ">=", ini.isoformat())],
                                  ["partner_id", "ref", "invoice_date", "amount_total", "company_id", "state"], company_ids=cids) if pids else []
            usados = set()
            hoy = dt.date.today()
            for r in rs:
                f = next((x for x in mv if x["partner_id"][0] == r["partner_id"] and x["company_id"][0] == r["empresa"] and x["id"] not in usados), None)
                if f:
                    usados.add(f["id"])
                    r.update(llegada=True, factura={"id": f["id"], "numero": f.get("ref") or "", "fecha": f.get("invoice_date") or "",
                                                    "total": f["amount_total"], "borrador": f["state"] == "draft"})
                elif r["id"] in marcas:
                    r.update(llegada=True, manual=marcas[r["id"]] or "Marcada a mano")
                else:
                    r["llegada"] = False
                    r["retraso"] = ini.year == hoy.year and ini.month == hoy.month and hoy.day > (r["dia"] or 31) or ini < hoy.replace(day=1)
            return {"mes": mes, "lista": rs}
        return guard(run)

    @app.post("/api/compras/recurrentes")
    async def recurrente_guardar(request: Request):
        u, cids = ctx(request)
        b = await request.json()
        emp = int(b.get("empresa") or cids[0])
        if emp not in cids:
            raise HTTPException(403, "Empresa no permitida")
        vals = (emp, b.get("empresa_nombre") or "", int(b["partner_id"]) if b.get("partner_id") else None, b.get("partner_nombre") or "",
                b.get("concepto") or "", float(b.get("importe") or 0), int(b.get("dia") or 0))
        with lock, con() as c:
            if b.get("id"):
                c.execute("UPDATE recurrentes SET empresa=?, empresa_nombre=?, partner_id=?, partner_nombre=?, concepto=?, importe=?, dia=? WHERE id=?",
                          vals + (int(b["id"]),))
            else:
                c.execute("INSERT INTO recurrentes (empresa, empresa_nombre, partner_id, partner_nombre, concepto, importe, dia) VALUES (?,?,?,?,?,?,?)", vals)
        return {"ok": True}

    @app.delete("/api/compras/recurrentes/{rid}")
    def recurrente_borrar(rid: int, request: Request):
        current_user(request)
        with lock, con() as c:
            c.execute("UPDATE recurrentes SET activo=0 WHERE id=?", (rid,))
        return {"ok": True}

    @app.post("/api/compras/recurrentes/{rid}/marca")
    async def recurrente_marca(rid: int, request: Request):
        current_user(request)
        b = await request.json()
        with lock, con() as c:
            if b.get("quitar"):
                c.execute("DELETE FROM recurrentes_marcas WHERE rid=? AND mes=?", (rid, b["mes"]))
            else:
                c.execute("INSERT OR REPLACE INTO recurrentes_marcas VALUES (?,?,?)", (rid, b["mes"], b.get("nota") or ""))
        return {"ok": True}

    @app.get("/api/compras/proveedores")
    def proveedores(request: Request, q: str = "", company: str = "all"):
        _, cids = ctx(request, company)
        if len(q.strip()) < 2:
            return []
        return guard(lambda: odoo.search_read("res.partner", ["|", ("name", "ilike", q), ("vat", "ilike", q)], ["display_name", "vat"],
                                              company_ids=cids, limit=12))
