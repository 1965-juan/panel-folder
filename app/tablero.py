"""Tablero de compras (tipo Trello): una columna por proveedor con su pedido de compra en borrador (uno solo por proveedor)
y una tarjeta por artículo. Arrastrando una tarjeta a otra columna el artículo pasa al pedido de ese proveedor.
Todo en DISTRIPLUS (empresa 1), que es quien compra.
"""
import threading

from fastapi import Request, HTTPException

EMPRESA = 1
TODAS = [1, 2, 3, 4, 5]
ABIERTOS = ["draft", "sent"]


def setup(app, *, odoo, current_user, scope, guard, odoo_url):
    lock = threading.Lock()
    cache = {}

    def ctx(request):
        u = current_user(request)
        cids, _ = scope(u, "all")
        if EMPRESA not in cids:
            raise HTTPException(403, "Sin acceso a Distriplus")
        return u

    def propias():
        if "propias" not in cache:
            cache["propias"] = {c["partner_id"][0]: c["id"] for c in odoo.search_read("res.company", [], ["partner_id"], company_ids=TODAS)}
        return cache["propias"]

    def campos_linea():
        if not cache.get("campos"):
            try:
                cache["campos"] = set(odoo.call("purchase.order.line", "fields_get", [EMPRESA], attributes=["type"]).keys())
            except Exception:
                return {"product_uom_id"}  # Odoo 19; se reintenta la próxima vez
        return cache["campos"]

    def pedidos_abiertos(partner_id=None):
        dom = [("state", "in", ABIERTOS), ("company_id", "=", EMPRESA)]
        if partner_id:
            dom.append(("partner_id", "=", int(partner_id)))
        return odoo.search_read("purchase.order", dom, ["name", "partner_id", "state", "origin", "amount_untaxed", "date_order", "order_line"],
                                company_ids=[EMPRESA], order="id")

    def pedido_de(partner_id):
        """El pedido en borrador de ese proveedor (el más antiguo); si no hay, se crea."""
        ps = [p for p in pedidos_abiertos(partner_id) if p["state"] == "draft"] or pedidos_abiertos(partner_id)
        if ps:
            return ps[0]["id"]
        r = odoo.call("purchase.order", "create", [EMPRESA], vals_list=[{"partner_id": int(partner_id), "company_id": EMPRESA}])
        return r[0] if isinstance(r, list) else r

    def uom():
        return "product_uom_id" if "product_uom_id" in campos_linea() else "product_uom"

    def copiar_linea(l, po_id, qty=None):
        vals = {"order_id": po_id, "product_id": l["product_id"][0], "product_qty": qty if qty is not None else l["product_qty"]}
        if l.get(uom()):
            vals[uom()] = l[uom()][0]
        cs = campos_linea()
        for k in ("sale_line_id", "sale_order_id"):
            if k in cs and l.get(k):
                vals[k] = l[k][0]
        r = odoo.call("purchase.order.line", "create", [EMPRESA], vals_list=[vals])
        return r[0] if isinstance(r, list) else r

    def leer_lineas(ids):
        cs = campos_linea()
        F = ["order_id", "product_id", "name", "product_qty", "qty_received", uom(), "price_unit", "price_subtotal", "display_type"] + [k for k in ("sale_line_id", "sale_order_id") if k in cs]
        return odoo.search_read("purchase.order.line", [("id", "in", ids)], F, company_ids=[EMPRESA]) if ids else []

    def anotar_origen(po_id, origen):
        if not origen:
            return
        p = odoo.search_read("purchase.order", [("id", "=", po_id)], ["origin"], company_ids=[EMPRESA])
        actual = (p[0].get("origin") or "") if p else ""
        partes = [x.strip() for x in actual.split(",") if x.strip()]
        for o in [x.strip() for x in origen.split(",") if x.strip()]:
            if o not in partes:
                partes.append(o)
        odoo.call("purchase.order", "write", [EMPRESA], ids=[po_id], vals={"origin": ", ".join(partes)[:500]})

    def juntar(partner_id):
        """Junta en un solo pedido todos los borradores de un proveedor."""
        ps = [p for p in pedidos_abiertos(partner_id) if p["state"] == "draft"]
        if len(ps) < 2:
            return 0
        dest = ps[0]["id"]
        n = 0
        for p in ps[1:]:
            for l in leer_lineas(p["order_line"]):
                if l.get("display_type"):
                    continue
                copiar_linea(l, dest)
                n += 1
            anotar_origen(dest, p.get("origin") or "")
            odoo.call("purchase.order", "button_cancel", [EMPRESA], ids=[p["id"]])
        return n

    @app.get("/api/tablero")
    def tablero(request: Request):
        ctx(request)

        def run():
            pos = pedidos_abiertos()
            import datetime as _dt
            desde = (_dt.date.today() - _dt.timedelta(days=60)).isoformat()
            lanz = odoo.search_read("purchase.order", [("state", "=", "purchase"), ("company_id", "=", EMPRESA), ("date_approve", ">=", desde),
                                                       ("receipt_status", "!=", "full")],
                                    ["name", "partner_id", "state", "origin", "amount_untaxed", "date_approve", "order_line", "receipt_status"],
                                    company_ids=[EMPRESA], order="date_approve desc")
            prop = propias()
            lanz = [p for p in lanz if p["partner_id"][0] not in prop]
            pos = pos + lanz
            lids = [i for p in pos for i in p["order_line"]]
            ls = [l for l in leer_lineas(lids) if not l.get("display_type")]
            pids = list({l["product_id"][0] for l in ls if l.get("product_id")})
            prods = {p["id"]: p for p in odoo.search_read("product.product", [("id", "in", pids)], ["default_code", "barcode", "product_tmpl_id", "qty_available"],
                                                          company_ids=[EMPRESA])} if pids else {}
            tmpls = list({p["product_tmpl_id"][0] for p in prods.values()})
            si = odoo.search_read("product.supplierinfo", [("product_tmpl_id", "in", tmpls)], ["partner_id", "product_tmpl_id", "price", "min_qty", "product_code"],
                                  company_ids=[EMPRESA], limit=20000) if tmpls else []
            por_tmpl = {}
            for s in si:
                por_tmpl.setdefault(s["product_tmpl_id"][0], {})
                prev = por_tmpl[s["product_tmpl_id"][0]].get(s["partner_id"][0])
                if not prev or (s["price"] and s["price"] < prev["precio"]):
                    por_tmpl[s["product_tmpl_id"][0]][s["partner_id"][0]] = {"partner_id": s["partner_id"][0], "proveedor": s["partner_id"][1], "precio": s["price"],
                                                                             "ref": s.get("product_code") or ""}
            cols, lanzados = {}, {}
            for p in pos:
                k = p["partner_id"][0]
                col = cols.setdefault(k, {"partner_id": k, "proveedor": p["partner_id"][1], "pedidos": [], "lineas": [], "total": 0, "lanzados": [],
                                          "grupo": k in prop, "mismo": prop.get(k) == EMPRESA})
                if p["state"] == "purchase":
                    lz = {"id": p["id"], "nombre": p["name"], "fecha": (p.get("date_approve") or "")[:10], "recepcion": p.get("receipt_status") or "",
                          "total": round(p.get("amount_untaxed") or 0, 2), "lineas": []}
                    col["lanzados"].append(lz)
                    lanzados[p["id"]] = lz
                else:
                    col["pedidos"].append({"id": p["id"], "nombre": p["name"], "estado": p["state"], "origen": p.get("origin") or ""})
                    col["total"] += p.get("amount_untaxed") or 0
            po_partner = {p["id"]: p["partner_id"][0] for p in pos}
            for l in ls:
                pr = prods.get(l["product_id"][0], {})
                alt = por_tmpl.get((pr.get("product_tmpl_id") or [0])[0], {})
                dst = lanzados[l["order_id"][0]]["lineas"] if l["order_id"][0] in lanzados else cols[po_partner[l["order_id"][0]]]["lineas"]
                dst.append({
                    "id": l["id"], "po_id": l["order_id"][0], "po": l["order_id"][1], "product_id": l["product_id"][0], "articulo": l["product_id"][1],
                    "ref": pr.get("default_code") or "", "cantidad": l["product_qty"], "ud": (l.get(uom()) or [0, ""])[1], "precio": l["price_unit"],
                    "importe": l["price_subtotal"], "stock": pr.get("qty_available") or 0, "recibido": l.get("qty_received") or 0,
                    "para": (l.get("sale_order_id") or [0, ""])[1] if l.get("sale_order_id") else "",
                    "proveedores": sorted(alt.values(), key=lambda x: (x["precio"] or 9e9))})
            out = sorted(cols.values(), key=lambda c: (c["grupo"], not c["lineas"], c["proveedor"].lower()))
            for c in out:
                c["total"] = round(c["total"], 2)
                c["lineas"].sort(key=lambda x: x["articulo"].lower())
                c["lanzados"].sort(key=lambda x: x["fecha"], reverse=True)
            return {"columnas": out, "odoo_url": odoo_url}
        return guard(run)

    @app.post("/api/tablero/juntar")
    async def juntar_todos(request: Request):
        """Deja un solo pedido en borrador por proveedor."""
        ctx(request)

        def run():
            with lock:
                vistos, n = set(), 0
                for p in pedidos_abiertos():
                    k = p["partner_id"][0]
                    if k in vistos:
                        continue
                    vistos.add(k)
                    n += juntar(k)
                return {"ok": True, "movidas": n}
        return guard(run)

    @app.post("/api/tablero/mover")
    async def mover(request: Request):
        """{line_id, partner_id}: el artículo pasa al pedido en borrador de otro proveedor."""
        ctx(request)
        b = await request.json()
        lid, partner = int(b.get("line_id") or 0), int(b.get("partner_id") or 0)
        if not lid or not partner:
            raise HTTPException(400, "Falta el artículo o el proveedor")

        def run():
            with lock:
                ls = leer_lineas([lid])
                if not ls:
                    raise HTTPException(404, "Ese artículo ya no está en el pedido (recarga el tablero)")
                l = ls[0]
                orig = odoo.search_read("purchase.order", [("id", "=", l["order_id"][0])], ["state", "partner_id", "origin", "order_line"], company_ids=[EMPRESA])[0]
                if orig["state"] not in ABIERTOS:
                    raise HTTPException(400, "Ese pedido ya está confirmado: no se puede cambiar de proveedor")
                if orig["partner_id"][0] == partner:
                    return {"ok": True, "igual": True}
                if propias().get(partner) == EMPRESA:
                    raise HTTPException(400, "No se le puede pedir a Distriplus a sí misma")
                dest = pedido_de(partner)
                nueva = copiar_linea(l, dest)
                if l.get("sale_order_id"):
                    anotar_origen(dest, l["sale_order_id"][1])
                odoo.call("purchase.order.line", "unlink", [EMPRESA], ids=[lid])
                if len(orig["order_line"]) <= 1:  # el pedido de origen se ha quedado vacío
                    odoo.call("purchase.order", "button_cancel", [EMPRESA], ids=[orig["id"]])
                return {"ok": True, "line_id": nueva, "po_id": dest}
        return guard(run)

    @app.post("/api/tablero/cantidad")
    async def cantidad(request: Request):
        ctx(request)
        b = await request.json()
        lid, q = int(b.get("line_id") or 0), float(str(b.get("cantidad") or 0).replace(",", "."))
        if q <= 0:
            raise HTTPException(400, "La cantidad tiene que ser mayor que 0 (para quitarlo usa ✕)")
        return guard(lambda: (odoo.call("purchase.order.line", "write", [EMPRESA], ids=[lid], vals={"product_qty": q}), {"ok": True})[1])

    @app.post("/api/tablero/quitar")
    async def quitar(request: Request):
        ctx(request)
        b = await request.json()
        lid = int(b.get("line_id") or 0)

        def run():
            ls = leer_lineas([lid])
            if not ls:
                return {"ok": True}
            p = odoo.search_read("purchase.order", [("id", "=", ls[0]["order_id"][0])], ["state", "order_line"], company_ids=[EMPRESA])[0]
            if p["state"] not in ABIERTOS:
                raise HTTPException(400, "Ese pedido ya está confirmado")
            odoo.call("purchase.order.line", "unlink", [EMPRESA], ids=[lid])
            if len(p["order_line"]) <= 1:
                odoo.call("purchase.order", "button_cancel", [EMPRESA], ids=[p["id"]])
            return {"ok": True}
        return guard(run)

    @app.post("/api/tablero/confirmar")
    async def confirmar(request: Request):
        """Confirma el pedido de un proveedor (pasa a pedido de compra; las entradas aparecen en Recepciones)."""
        ctx(request)
        b = await request.json()
        po = int(b.get("po_id") or 0)

        def run():
            p = odoo.search_read("purchase.order", [("id", "=", po)], ["state", "name", "order_line"], company_ids=[EMPRESA])
            if not p or p[0]["state"] not in ABIERTOS:
                raise HTTPException(400, "Ese pedido no está en borrador")
            if not p[0]["order_line"]:
                raise HTTPException(400, "El pedido está vacío")
            odoo.call("purchase.order", "button_confirm", [EMPRESA], ids=[po])
            return {"ok": True, "nombre": p[0]["name"]}
        return guard(run)

    @app.get("/api/tablero/proveedores")
    def proveedores(request: Request, q: str = ""):
        ctx(request)
        q = q.strip()
        if len(q) < 2:
            return []
        prop = propias()
        return guard(lambda: [{"id": p["id"], "nombre": p["display_name"]} for p in
                              odoo.search_read("res.partner", [("is_company", "=", True), "|", ("name", "ilike", q), ("vat", "ilike", q)],
                                               ["display_name", "supplier_rank"], company_ids=TODAS, limit=20, order="supplier_rank desc, name")
                              if prop.get(p["id"]) != EMPRESA])
