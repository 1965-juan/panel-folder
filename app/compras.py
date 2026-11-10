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
        try:
            c.execute("ALTER TABLE docs ADD COLUMN lectura TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE docs ADD COLUMN compra TEXT")
        except sqlite3.OperationalError:
            pass
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
                    act = odoo.call("account.journal", "create_document_from_attachment", [cid], ids=[jr[0]["id"]], attachment_ids=[att],
                                    context={"default_move_type": "in_invoice", "allowed_company_ids": [cid]})
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
            out = []
            for r in c.execute(q + " ORDER BY subido DESC LIMIT 200", args).fetchall():
                d = dict(r)
                d.pop("lectura", None)
                d["compra"] = json.loads(d["compra"]) if d.get("compra") else None
                out.append(d)
            return out

    @app.get("/api/compras/pdf/{did}")
    def pdf(did: str, request: Request):
        current_user(request)
        with con() as c:
            r = c.execute("SELECT fichero, nombre FROM docs WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404)
        return FileResponse(carpeta / r["fichero"], media_type="application/pdf", filename=r["nombre"],
                            content_disposition_type="inline")

    def leer_con_claude(datos):
        import os as _os
        import httpx as _hx
        cf = data_dir / "claude_clave.txt"
        k = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", cf.read_text(errors="ignore")) if cf.exists() else []
        if not k:
            raise HTTPException(400, "Falta la clave de Claude en el NAS")
        pide = ("Lee este documento de proveedor (factura, albarán o confirmación de pedido) y devuelve SOLO un JSON con: "
                '{"tipo": "factura|albaran|pedido", "proveedor": "", "nif_proveedor": "", "numero": "", "fecha": "AAAA-MM-DD", '
                '"lineas": [{"ref": "", "ean": "", "descripcion": "", "cantidad": 0, "precio": 0, "descuento": 0, "iva": 21, "importe": 0}], '
                '"base": 0, "iva_total": 0, "total": 0, "portes": 0}. Precios unitarios sin IVA y ya con el descuento de línea aplicado en "precio" '
                'solo si el documento no muestra el descuento aparte. Números con punto decimal. No inventes nada: si un dato no está, déjalo vacío.')
        r = _hx.post(_os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages", timeout=180, headers={
            "x-api-key": k[0], "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": _os.environ.get("PANEL_MODEL", "claude-sonnet-5-5"), "max_tokens": 8000, "messages": [{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(datos).decode()}},
                {"type": "text", "text": pide}]}]})
        if r.status_code != 200:
            raise HTTPException(502, f"Claude respondió {r.status_code}: {r.text[:200]}")
        txt = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        m = re.search(r"\{[\s\S]*\}", txt)
        return json.loads(m.group(0)) if m else {"error": txt[:500]}

    @app.post("/api/compras/docs/{did}/leer")
    def doc_leer(did: str, request: Request):
        """Lee el PDF con Claude y devuelve sus datos (proveedor, número, líneas…)."""
        current_user(request)
        with con() as c:
            r = c.execute("SELECT fichero FROM docs WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404)
        datos = leer_con_claude((carpeta / r["fichero"]).read_bytes())
        with lock, con() as c:
            c.execute("UPDATE docs SET lectura=? WHERE id=?", (json.dumps(datos, ensure_ascii=False), did))
        return datos

    @app.post("/api/compras/docs/{did}/a_factura")
    def doc_a_factura(did: str, request: Request, company: str = "all"):
        """Un PDF subido como albarán que en realidad es una factura: lo manda a Odoo como factura en borrador."""
        u, cid, cids = una_empresa(request, company)
        with con() as c:
            r = c.execute("SELECT * FROM docs WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404)
        datos = (carpeta / r["fichero"]).read_bytes()

        def run():
            emp = r["empresa"] or cid
            jr = odoo.search_read("account.journal", [("type", "=", "purchase"), ("company_id", "=", emp)], ["id"], company_ids=[emp], limit=1)
            att = odoo.call("ir.attachment", "create", [emp], vals_list=[{"name": r["nombre"], "datas": base64.b64encode(datos).decode(), "mimetype": "application/pdf"}])
            att = att[0] if isinstance(att, list) else att
            act = odoo.call("account.journal", "create_document_from_attachment", [emp], ids=[jr[0]["id"]], attachment_ids=[att],
                                context={"default_move_type": "in_invoice", "allowed_company_ids": [emp]})
            oid = (act or {}).get("res_id")
            with lock, con() as c:
                c.execute("UPDATE docs SET tipo='factura', estado='en_odoo', odoo_model='account.move', odoo_id=? WHERE id=?", (oid, did))
            return {"ok": True, "factura_id": oid}
        return guard(run)

    # ---------- PDF -> pedido de compra (y factura en borrador enlazada) ----------
    def _doc(did):
        with con() as c:
            r = c.execute("SELECT * FROM docs WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404, "Documento no encontrado")
        return r

    def _lectura(r, releer=False):
        if r["lectura"] and not releer:
            return json.loads(r["lectura"])
        datos = leer_con_claude((carpeta / r["fichero"]).read_bytes())
        if datos.get("error"):
            raise HTTPException(502, "No he podido leer el PDF: " + str(datos["error"])[:200])
        with lock, con() as c:
            c.execute("UPDATE docs SET lectura=? WHERE id=?", (json.dumps(datos, ensure_ascii=False), r["id"]))
        return datos

    def _num(v):
        try:
            return float(str(v).replace(",", ".")) if v not in (None, "") else 0.0
        except ValueError:
            return 0.0

    def _buscar_proveedor(lec, cids):
        nif = re.sub(r"[^A-Z0-9]", "", (lec.get("nif_proveedor") or "").upper())
        if nif.startswith("ES"):
            nif = nif[2:]
        if len(nif) >= 8:
            ps = odoo.search_read("res.partner", [("vat", "ilike", nif), ("parent_id", "=", False)], ["display_name", "vat"], company_ids=cids, limit=5)
            if ps:
                return ps[0]
        nom = (lec.get("proveedor") or "").strip()
        palabras = [w for w in re.findall(r"[\wÁÉÍÓÚÑáéíóúñ]{4,}", nom) if w.upper() not in ("S.L.", "SLU", "S.A.", "SOCIEDAD", "LIMITADA")]
        if palabras:
            ps = odoo.search_read("res.partner", [("name", "ilike", palabras[0]), ("parent_id", "=", False)], ["display_name", "vat"], company_ids=cids, limit=5)
            if len(ps) == 1:
                return ps[0]
        return None

    VACIAS = {"de", "del", "la", "el", "con", "para", "por", "y", "en", "unidad", "unidades", "pack", "caja", "color", "colores",
              "rotulador", "boligrafo", "bolígrafo", "cartucho", "toner", "tóner", "tinta", "original", "compatible", "generico", "genérico",
              "negro", "azul", "rojo", "verde", "amarillo", "cyan", "magenta", "permanente", "marcador", "punta", "redonda", "biselada"}

    def _sugerencias(ref, desc, cids):
        """Artículos parecidos para que el usuario elija (no se casan solos para no equivocarse)."""
        vistos, out = set(), []

        def add(rs):
            for p in rs:
                if p["id"] not in vistos and len(out) < 6:
                    vistos.add(p["id"]); out.append({"id": p["id"], "name": p["display_name"]})
        palabras = [w for w in re.findall(r"[\w/\-\.]+", desc.lower()) if w not in VACIAS and len(w) >= 2]
        nums = [w for w in palabras if re.search(r"\d", w)]
        txt = [w for w in palabras if not re.search(r"\d", w)]
        # marca + modelo (p. ej. "edding" + "500", "stabilo" "boss" "70")
        claves = (txt[:1] + nums[:1]) if nums else txt[:2]
        for intento in (txt[:2] + nums[:1], claves):
            if len(intento) >= 2 and len(out) < 6:
                dom = [("purchase_ok", "=", True)] + [("name", "ilike", w) for w in intento]
                add(odoo.search_read("product.product", dom, ["display_name"], company_ids=cids, limit=6))
        if ref and len(ref) >= 4 and len(out) < 6:
            add(odoo.search_read("product.product", ["|", ("default_code", "ilike", ref), ("name", "ilike", ref)], ["display_name"], company_ids=cids, limit=4))
        return out

    def _casar_lineas(lineas, partner_id, cids):
        out = []
        for l in lineas:
            ref, ean, desc = str(l.get("ref") or "").strip(), re.sub(r"\D", "", str(l.get("ean") or "")), (l.get("descripcion") or "").strip()
            prod, como = None, None
            if partner_id and ref:
                si = odoo.search_read("product.supplierinfo", [("partner_id", "=", partner_id), ("product_code", "=", ref)], ["product_id", "product_tmpl_id"], company_ids=cids, limit=1)
                if si:
                    if si[0].get("product_id"):
                        prod = {"id": si[0]["product_id"][0], "name": si[0]["product_id"][1]}
                    else:
                        pp = odoo.search_read("product.product", [("product_tmpl_id", "=", si[0]["product_tmpl_id"][0])], ["display_name"], company_ids=cids, limit=1)
                        prod = pp and {"id": pp[0]["id"], "name": pp[0]["display_name"]}
                    como = "ref. proveedor"
            if not prod and len(ean) >= 8:
                pp = odoo.search_read("product.product", [("barcode", "=", ean)], ["display_name"], company_ids=cids, limit=1)
                if pp:
                    prod, como = {"id": pp[0]["id"], "name": pp[0]["display_name"]}, "EAN"
            if not prod and ref:
                pp = odoo.search_read("product.product", [("default_code", "=ilike", ref)], ["display_name"], company_ids=cids, limit=2)
                if len(pp) == 1:
                    prod, como = {"id": pp[0]["id"], "name": pp[0]["display_name"]}, "referencia"
            if not prod and len(desc) >= 6:
                pp = odoo.search_read("product.product", [("name", "=ilike", desc), ("purchase_ok", "=", True)], ["display_name"], company_ids=cids, limit=2)
                if len(pp) == 1:
                    prod, como = {"id": pp[0]["id"], "name": pp[0]["display_name"]}, "nombre"
            sug = []
            if not prod:
                sug = _sugerencias(ref, desc, cids)
            out.append({"ref": ref, "ean": ean, "descripcion": desc, "cantidad": _num(l.get("cantidad")), "precio": _num(l.get("precio")),
                        "descuento": _num(l.get("descuento")), "iva": _num(l.get("iva")) or 21.0,
                        "product_id": prod["id"] if prod else None, "producto": prod["name"] if prod else None, "como": como,
                        "sugerencias": sug})
        return out

    @app.post("/api/compras/docs/{did}/preparar")
    def doc_preparar(did: str, request: Request, releer: int = 0):
        """Lee el PDF y propone el pedido de compra: proveedor, líneas casadas con artículos de Odoo y pedidos abiertos del proveedor."""
        u, cids = ctx(request, "all")
        r = _doc(did)
        if r["empresa"] not in cids:
            raise HTTPException(403, "Documento de otra empresa")

        def run():
            lec = _lectura(r, bool(releer))
            # ¿ya está en compras? (pedido con la misma ref. del proveedor, o factura con ese número enlazada a un pedido)
            num = (lec.get("numero") or "").strip()
            if num and not r["compra"]:
                ya = odoo.search_read("purchase.order", [("company_id", "=", r["empresa"]), ("partner_ref", "=ilike", num), ("state", "!=", "cancel")],
                                      ["name", "picking_ids"], company_ids=[r["empresa"]], limit=1)
                if not ya:
                    fx = odoo.search_read("account.move", [("company_id", "=", r["empresa"]), ("move_type", "=", "in_invoice"), ("ref", "=ilike", num)],
                                          ["invoice_origin"], company_ids=[r["empresa"]], limit=1)
                    if fx and fx[0].get("invoice_origin"):
                        ya = odoo.search_read("purchase.order", [("company_id", "=", r["empresa"]), ("name", "=", fx[0]["invoice_origin"].split(",")[0].strip())],
                                              ["name", "picking_ids"], company_ids=[r["empresa"]], limit=1)
                if ya:
                    pk = odoo.search_read("stock.picking", [("id", "in", ya[0]["picking_ids"])], ["name"], company_ids=[r["empresa"]]) if ya[0]["picking_ids"] else []
                    comp = {"pedido": ya[0]["name"], "pedido_id": ya[0]["id"], "recepciones": [x["name"] for x in pk], "ya_estaba": True}
                    with lock, con() as c:
                        c.execute("UPDATE docs SET compra=? WHERE id=?", (json.dumps(comp, ensure_ascii=False), r["id"]))
                    return {"doc": {"id": r["id"], "nombre": r["nombre"], "tipo": r["tipo"]}, "compra": comp}
            prov = _buscar_proveedor(lec, cids)
            lineas = _casar_lineas(lec.get("lineas") or [], prov and prov["id"], cids)
            abiertos = []
            if prov:
                abiertos = [{"id": p["id"], "name": p["name"], "fecha": (p.get("date_order") or "")[:10], "total": p["amount_untaxed"], "estado": p["receipt_status"]}
                            for p in odoo.search_read("purchase.order", [("partner_id", "child_of", prov["id"]), ("company_id", "=", r["empresa"]),
                                                                         ("state", "in", ["draft", "sent", "purchase"]), ("receipt_status", "!=", "full")],
                                                      ["name", "date_order", "amount_untaxed", "receipt_status"], company_ids=[r["empresa"]], limit=10, order="id desc")]
            return {"doc": {"id": r["id"], "nombre": r["nombre"], "tipo": r["tipo"], "empresa": r["empresa"], "odoo_id": r["odoo_id"]},
                    "lectura": {k: lec.get(k) for k in ("tipo", "proveedor", "nif_proveedor", "numero", "fecha", "base", "iva_total", "total", "portes")},
                    "proveedor": prov, "lineas": lineas, "pedidos_abiertos": abiertos,
                    "compra": json.loads(r["compra"]) if r["compra"] else None}
        return guard(run)

    _campos = {}

    def _tiene_campo(model, campo, cid):
        if model not in _campos:
            _campos[model] = set(odoo.call(model, "fields_get", [cid], attributes=["type"]).keys())
        return campo in _campos[model]

    def _impuesto(cid, iva, cache={}):
        k = (cid, round(iva, 2))
        if k not in cache:
            ts = odoo.search_read("account.tax", [("type_tax_use", "=", "purchase"), ("amount", "=", iva), ("company_id", "=", cid), ("amount_type", "=", "percent")],
                                  ["name"], company_ids=[cid], order="sequence, id")
            pref = [t for t in ts if re.search(r"\bG\b|bienes", t["name"], re.I)] or [t for t in ts if not re.search(r"servic|invers|intra|import|ISP|exent|recargo", t["name"], re.I)] or ts
            cache[k] = pref[0]["id"] if pref else None
        return cache[k]

    @app.post("/api/compras/docs/{did}/a_compras")
    async def doc_a_compras(did: str, request: Request):
        """Crea (y confirma) el pedido de compra con las líneas revisadas. La recepción queda para que almacén la compruebe.
        Si el documento es una factura, rellena la factura en borrador enlazada al pedido para que Mayte le dé el OK."""
        u, cids = ctx(request, "all")
        b = await request.json()
        r = _doc(did)
        if r["empresa"] not in cids:
            raise HTTPException(403, "Documento de otra empresa")
        if r["compra"]:
            raise HTTPException(400, "Este documento ya se pasó a compras: " + json.loads(r["compra"]).get("pedido", ""))
        partner_id = int(b.get("partner_id") or 0)
        lineas = [l for l in (b.get("lineas") or []) if _num(l.get("cantidad")) > 0]
        if not partner_id:
            raise HTTPException(400, "Falta el proveedor")
        if not lineas:
            raise HTTPException(400, "No hay líneas con cantidad")
        cid = r["empresa"]
        c1 = [cid]

        def run():
            lec = json.loads(r["lectura"]) if r["lectura"] else {}
            avisos, creados = [], []
            # 1) artículos nuevos
            for l in lineas:
                if l.get("product_id"):
                    continue
                if not l.get("crear"):
                    raise HTTPException(400, f"La línea \"{l.get('descripcion')}\" no tiene artículo: elígelo o marca crearlo")
                vals = {"name": l.get("descripcion") or l.get("ref") or "Artículo nuevo", "purchase_ok": True, "sale_ok": True,
                        "standard_price": _num(l.get("precio")), "type": "consu",
                        "seller_ids": [[0, 0, {"partner_id": partner_id, "product_code": l.get("ref") or False, "price": _num(l.get("precio")), "company_id": cid}]]}
                if _tiene_campo("product.product", "is_storable", cid):
                    vals["is_storable"] = True
                if l.get("ref") and not odoo.search_read("product.product", [("default_code", "=", l["ref"])], ["id"], company_ids=cids, limit=1):
                    vals["default_code"] = l["ref"]
                ean = re.sub(r"\D", "", str(l.get("ean") or ""))
                if len(ean) >= 8 and not odoo.search_read("product.product", [("barcode", "=", ean)], ["id"], company_ids=cids, limit=1):
                    vals["barcode"] = ean
                pid = odoo.call("product.product", "create", c1, vals_list=[vals])
                l["product_id"] = pid[0] if isinstance(pid, list) else pid
                creados.append(vals["name"])
            # 2) ref. del proveedor en artículos que no la tenían (para casarlos solos la próxima vez)
            for l in lineas:
                if l.get("ref") and l.get("como") in ("EAN", "referencia", "nombre", "a mano"):
                    tm = odoo.search_read("product.product", [("id", "=", int(l["product_id"]))], ["product_tmpl_id"], company_ids=c1)
                    if tm and not odoo.search_read("product.supplierinfo", [("partner_id", "=", partner_id), ("product_tmpl_id", "=", tm[0]["product_tmpl_id"][0])], ["id"], company_ids=c1, limit=1):
                        try:
                            odoo.call("product.supplierinfo", "create", c1, vals_list=[{"partner_id": partner_id, "product_tmpl_id": tm[0]["product_tmpl_id"][0],
                                                                                        "product_code": l["ref"], "price": _num(l.get("precio")), "company_id": cid}])
                        except Exception:
                            pass
            # 3) pedido de compra
            ftax = "tax_ids" if _tiene_campo("purchase.order.line", "tax_ids", cid) else "taxes_id"
            olines = []
            for l in lineas:
                t = _impuesto(cid, _num(l.get("iva")) or 21.0)
                v = {"product_id": int(l["product_id"]), "product_qty": _num(l["cantidad"]), "price_unit": _num(l.get("precio")),
                     ftax: [[6, 0, [t] if t else []]]}
                if _num(l.get("descuento")):
                    v["discount"] = _num(l.get("descuento"))
                if l.get("descripcion"):
                    v["name"] = (f"[{l['ref']}] " if l.get("ref") else "") + l["descripcion"]
                olines.append([0, 0, v])
            portes = _num(lec.get("portes"))
            if portes:
                pp = odoo.search_read("product.product", [("name", "ilike", "portes"), ("purchase_ok", "=", True)], ["id"], company_ids=c1, limit=1)
                if pp:
                    olines.append([0, 0, {"product_id": pp[0]["id"], "product_qty": 1, "price_unit": portes, ftax: [[6, 0, [_impuesto(cid, 21.0)]]]}])
                else:
                    avisos.append(f"Portes de {portes:.2f} € sin artículo \"Portes\" en Odoo: añádelos a mano en la factura")
            po_vals = {"partner_id": partner_id, "company_id": cid, "order_line": olines, "partner_ref": lec.get("numero") or r["nombre"]}
            po = odoo.call("purchase.order", "create", c1, vals_list=[po_vals])
            po = po[0] if isinstance(po, list) else po
            odoo.call("purchase.order", "button_confirm", c1, ids=[po])
            pod = odoo.search_read("purchase.order", [("id", "=", po)], ["name", "picking_ids", "order_line", "amount_untaxed"], company_ids=c1)[0]
            picks = odoo.search_read("stock.picking", [("id", "in", pod["picking_ids"])], ["name"], company_ids=c1) if pod["picking_ids"] else []
            odoo.call("ir.attachment", "create", c1, vals_list=[{"name": r["nombre"] or "documento.pdf", "res_model": "purchase.order", "res_id": po,
                                                                  "mimetype": "application/pdf", "datas": base64.b64encode((carpeta / r["fichero"]).read_bytes()).decode()}])
            # 4) factura en borrador enlazada (solo si el PDF es una factura)
            factura = None
            if r["tipo"] == "factura" or (lec.get("tipo") or "").lower() == "factura":
                mid = r["odoo_id"] if r["odoo_model"] == "account.move" else None
                if mid:
                    st = odoo.search_read("account.move", [("id", "=", mid)], ["state"], company_ids=c1)
                    if not st or st[0]["state"] != "draft":
                        mid = None
                if not mid:
                    jr = odoo.search_read("account.journal", [("type", "=", "purchase"), ("company_id", "=", cid)], ["id"], company_ids=c1, limit=1)
                    mid = odoo.call("account.move", "create", c1, vals_list=[{"move_type": "in_invoice", "journal_id": jr[0]["id"], "partner_id": partner_id}],
                                    context={"default_move_type": "in_invoice"})
                    mid = mid[0] if isinstance(mid, list) else mid
                    odoo.call("ir.attachment", "create", c1, vals_list=[{"name": r["nombre"] or "factura.pdf", "res_model": "account.move", "res_id": mid,
                                                                          "mimetype": "application/pdf", "datas": base64.b64encode((carpeta / r["fichero"]).read_bytes()).decode()}])
                pls = odoo.search_read("purchase.order.line", [("order_id", "=", po)], ["product_id", "product_qty", "price_unit", "discount", ftax, "name"], company_ids=c1)
                ilines = [[5, 0, 0]] + [[0, 0, {"purchase_line_id": pl["id"], "product_id": pl["product_id"][0], "name": pl["name"], "quantity": pl["product_qty"],
                                                  "price_unit": pl["price_unit"], "discount": pl.get("discount") or 0, "tax_ids": [[6, 0, pl[ftax]]]}] for pl in pls]
                vals = {"partner_id": partner_id, "invoice_line_ids": ilines, "invoice_origin": pod["name"]}
                if lec.get("numero"):
                    vals["ref"] = lec["numero"]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", lec.get("fecha") or ""):
                    vals["invoice_date"] = lec["fecha"]
                odoo.call("account.move", "write", c1, ids=[mid], vals=vals,
                          context={"default_move_type": "in_invoice"})
                fm = odoo.search_read("account.move", [("id", "=", mid)], ["amount_total"], company_ids=c1)
                factura = {"id": mid, "total": fm[0]["amount_total"] if fm else None}
                if fm and _num(lec.get("total")) and abs(fm[0]["amount_total"] - _num(lec["total"])) > 0.02:
                    avisos.append(f"El total de la factura en Odoo ({fm[0]['amount_total']:.2f} €) no cuadra con el PDF ({_num(lec['total']):.2f} €): Mayte que lo revise")
            res = {"pedido": pod["name"], "pedido_id": po, "recepciones": [p["name"] for p in picks], "factura": factura,
                   "creados": creados, "avisos": avisos, "por": u, "cuando": time.time()}
            with lock, con() as c:
                # el albarán sigue "por recibir" para que almacén lo elija al comprobar la recepción
                c.execute("UPDATE docs SET compra=? WHERE id=?", (json.dumps(res, ensure_ascii=False), did))
                if factura:
                    c.execute("UPDATE docs SET odoo_model='account.move', odoo_id=? WHERE id=?", (factura["id"], did))
            return res
        return guard(run)

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


def setup_fotos(app, *, odoo, current_user, scope, guard):
    """Subir el Excel de un proveedor con fotos incrustadas (como el de Depesche) y ponerlas en los artículos por EAN."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    from fastapi.responses import HTMLResponse

    NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

    def leer_excel(datos):
        z = zipfile.ZipFile(io.BytesIO(datos))
        ss = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                ss.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        filas = {}
        for row in sheet.iter("{%s}row" % NS["m"]):
            n = int(row.get("r"))
            for c in row.findall("m:c", NS):
                col = re.match(r"[A-Z]+", c.get("r")).group(0)
                v = c.find("m:v", NS)
                val = (v.text if v is not None else "") or ""
                if c.get("t") == "s" and val:
                    val = ss[int(val)]
                elif c.get("t") == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter("{%s}t" % NS["m"]))
                filas.setdefault(n, {})[col] = val.strip()
        cab = filas.get(1, {})
        col_ean = next((k for k, v in cab.items() if "EAN" in v.upper() or "BARRAS" in v.upper()), None)
        col_ref = next((k for k, v in cab.items() if v.upper().replace(".", "") in ("ART NO", "ARTNO", "REF", "REFERENCIA", "CÓDIGO", "CODIGO")), None)
        # imágenes y su fila
        rels = ET.fromstring(z.read("xl/drawings/_rels/drawing1.xml.rels"))
        rid2f = {r.get("Id"): "xl/drawings/" + r.get("Target") for r in rels}
        dr = ET.fromstring(z.read("xl/drawings/drawing1.xml"))
        fotos = []
        for anc in list(dr):
            fr = anc.find("xdr:from", NS)
            blip = anc.find(".//a:blip", NS)
            if fr is None or blip is None:
                continue
            fila = int(fr.find("xdr:row", NS).text) + 1
            f = rid2f.get(blip.get("{%s}embed" % NS["r"]), "")
            f = f.replace("xl/drawings/../", "xl/")
            if f in z.namelist():
                fotos.append((fila, z.read(f)))
        return [{"fila": fi, "ean": filas.get(fi, {}).get(col_ean, "") if col_ean else "", "ref": filas.get(fi, {}).get(col_ref, "") if col_ref else "",
                 "foto": b} for fi, b in fotos], bool(col_ean)

    @app.post("/api/compras/fotos_excel")
    async def fotos_excel(request: Request, archivo: UploadFile = File(...), sobrescribir: str = Form("no")):
        u = current_user(request)
        cids, _ = scope(u, "all")
        datos = await archivo.read()

        def run():
            try:
                items, hay_ean = leer_excel(datos)
            except Exception as e:
                raise HTTPException(400, f"No he podido leer el Excel: {e}")
            if not hay_ean:
                raise HTTPException(400, "El Excel no tiene una columna EAN / código de barras")
            out = {"fotos": len(items), "puestas": 0, "ya_tenian": 0, "no_encontrados": []}
            for it in items:
                ean = re.sub(r"\D", "", it["ean"])
                if not ean:
                    continue
                p = odoo.search_read("product.product", ["|", ("barcode", "=", ean), ("barcode", "=", "0" + ean)],
                                     ["product_tmpl_id", "image_128"], company_ids=cids, limit=1)
                if not p:
                    out["no_encontrados"].append(it["ref"] or ean)
                    continue
                if p[0].get("image_128") and sobrescribir != "si":
                    out["ya_tenian"] += 1
                    continue
                odoo.call("product.template", "write", cids, ids=[p[0]["product_tmpl_id"][0]], vals={"image_1920": base64.b64encode(it["foto"]).decode()})
                out["puestas"] += 1
            return out
        return guard(run)

    @app.get("/compras/fotos")
    def fotos_pagina(request: Request):
        current_user(request)
        return HTMLResponse("""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Fotos desde Excel</title>
<style>body{font:15px -apple-system,Segoe UI,sans-serif;background:#3A1F73;display:grid;place-items:center;min-height:100vh;margin:0}
.c{background:#fff;border-radius:14px;padding:28px;max-width:460px;display:flex;flex-direction:column;gap:12px}h2{margin:0;color:#3A1F73}
button{background:#F39A2B;color:#fff;border:0;border-radius:8px;padding:11px;font-weight:700;font-size:15px;cursor:pointer}#r{font-weight:600;white-space:pre-wrap}</style>
<div class=c><h2>Fotos de artículos desde Excel</h2><p>Sube el Excel del proveedor que trae las fotos dentro (por ejemplo el de Depesche). Cada foto se pone en el artículo de Odoo con el mismo código de barras (EAN).</p>
<input type=file id=f accept=".xlsx"><label><input type=checkbox id=s> Cambiar también las fotos que ya tengan</label><button id=b>Poner fotos</button><p id=r></p><a href="/#compras">← Volver al panel</a></div>
<script>b.onclick=async()=>{if(!f.files[0])return r.textContent='Elige el Excel';r.textContent='Subiendo y poniendo fotos…';const fd=new FormData();fd.append('archivo',f.files[0]);fd.append('sobrescribir',s.checked?'si':'no');
const x=await fetch('/api/compras/fotos_excel',{method:'POST',body:fd});const j=await x.json();
r.textContent=x.ok?`Hecho: ${j.puestas} fotos puestas de ${j.fotos}.`+(j.ya_tenian?`\\n${j.ya_tenian} ya tenían foto (no tocadas).`:'')+(j.no_encontrados.length?`\\nNo están en Odoo: ${j.no_encontrados.join(', ')}`:''):'Error: '+(j.detail||x.status)}</script>""")
