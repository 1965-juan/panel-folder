"""Tablero de compras (tipo Trello): una columna por proveedor con su pedido de compra en borrador (uno solo por proveedor)
y una tarjeta por artículo. Arrastrando una tarjeta a otra columna el artículo pasa al pedido de ese proveedor.
Todo en DISTRIPLUS (empresa 1), que es quien compra.
"""
import threading

import html as _h
import re as _re

from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse

EMPRESA = 1
TODAS = [1, 2, 3, 4, 5]
ABIERTOS = ["draft", "sent"]
# stock que se enseña al imprimir: (título, empresa, ubicación)
STOCKS = [("Almacén", 1, 8), ("Tienda 1", 1, 34), ("Tienda 2", 1, 40), ("T. Logipaper", 4, 46)]


def setup(app, *, odoo, current_user, scope, guard, odoo_url, data_dir=None):
    lock = threading.Lock()
    import json as _json
    oc_file = (data_dir / "tablero_ocultos.json") if data_dir else None

    def ocultos():
        try:
            return set(_json.loads(oc_file.read_text())) if oc_file and oc_file.exists() else set()
        except Exception:
            return set()
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
            oc = ocultos()
            pos = [p for p in pos if p["id"] not in oc]   # pedidos que no son de material (imprenta, servicios…)
            lids = [i for p in pos for i in p["order_line"]]
            ls = [l for l in leer_lineas(lids) if not l.get("display_type")]
            pids = list({l["product_id"][0] for l in ls if l.get("product_id")})
            prods = {p["id"]: p for p in odoo.search_read("product.product", [("id", "in", pids)], ["default_code", "barcode", "product_tmpl_id", "qty_available"],
                                                          company_ids=[EMPRESA])} if pids else {}
            tmpls = list({p["product_tmpl_id"][0] for p in prods.values()})
            # stock de Tienda Logipaper (VP/Stock): para lo que se pide a Logipaper, por si se ha agotado y conviene pedirlo a otro proveedor
            vp = {}
            pids_lg = list({l["product_id"][0] for l in ls if l.get("product_id") and prop.get(next((p["partner_id"][0] for p in pos if p["id"] == l["order_id"][0]), 0)) == 4})
            if pids_lg:
                try:
                    for q in odoo.search_read("stock.quant", [("product_id", "in", pids_lg), ("location_id", "child_of", 46)], ["product_id", "quantity", "reserved_quantity"],
                                              company_ids=[4]):
                        a = vp.setdefault(q["product_id"][0], [0, 0])
                        a[0] += q["quantity"]; a[1] += q.get("reserved_quantity") or 0
                except Exception:
                    vp = {}
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
                                          "grupo": k in prop, "mismo": prop.get(k) == EMPRESA, "empresa_grupo": prop.get(k)})
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
                    "tmpl_id": (pr.get("product_tmpl_id") or [0])[0],
                    "ref": pr.get("default_code") or "", "cantidad": l["product_qty"], "ud": (l.get(uom()) or [0, ""])[1], "precio": l["price_unit"],
                    "importe": l["price_subtotal"], "stock": pr.get("qty_available") or 0,
                    # el stock de Tienda Logipaper solo se avisa en las líneas pedidas al proveedor Logipaper
                    "stock_vp": (None if prop.get(po_partner[l["order_id"][0]]) != 4 else
                                 round(vp[l["product_id"][0]][0], 2) if l["product_id"][0] in vp else 0),
                    "reservado_vp": round(vp.get(l["product_id"][0], [0, 0])[1], 2), "recibido": l.get("qty_received") or 0,
                    "para": (l.get("sale_order_id") or [0, ""])[1] if l.get("sale_order_id") else "",
                    "proveedores": sorted(alt.values(), key=lambda x: (x["precio"] or 9e9))})
            out = sorted(cols.values(), key=lambda c: (c["grupo"], not c["lineas"], c["proveedor"].lower()))
            for c in out:
                c["total"] = round(c["total"], 2)
                c["lineas"].sort(key=lambda x: x["articulo"].lower())
                c["lanzados"].sort(key=lambda x: x["fecha"], reverse=True)
            return {"columnas": out, "odoo_url": odoo_url}
        return guard(run)

    @app.post("/api/tablero/abrir")
    async def abrir(request: Request):
        """{partner_id}: el pedido que se va acumulando de ese proveedor (lo crea si no hay) para añadirle material en Odoo."""
        ctx(request)
        b = await request.json()
        pid = int(b.get("partner_id") or 0)
        if not pid:
            raise HTTPException(400, "Falta el proveedor")
        def run():
            po = pedido_de(pid)
            return {"po_id": po, "url": f"{odoo_url}/odoo/action-purchase.purchase_rfq/{po}"}
        return guard(run)

    @app.get("/tablero/imprimir/{po_id}", response_class=HTMLResponse)
    def imprimir(po_id: int, request: Request):
        """Hoja del pedido de compra para pasarla al proveedor: ref. Folder, ref. proveedor, descripción y cantidad,
        y al final (opcional) el stock de almacén y de las 3 tiendas por si hay que pedir alguna unidad más."""
        ctx(request)

        def run():
            po = odoo.search_read("purchase.order", [("id", "=", po_id), ("company_id", "=", EMPRESA)],
                                  ["name", "partner_id", "date_order", "date_approve", "state", "order_line"], company_ids=[EMPRESA])
            if not po:
                raise HTTPException(404, "No encuentro el pedido")
            po = po[0]
            ls = [l for l in leer_lineas(po["order_line"]) if not l.get("display_type") and l.get("product_id")]
            pids = list({l["product_id"][0] for l in ls})
            pr = {p["id"]: p for p in odoo.search_read("product.product", [("id", "in", pids)], ["default_code", "name", "product_tmpl_id", "x_studio_manufacturer_ref"],
                                                        company_ids=TODAS)} if pids else {}
            part = odoo.search_read("res.partner", [("id", "=", po["partner_id"][0])], ["commercial_partner_id", "email", "phone"], company_ids=TODAS)[0]
            provs = {po["partner_id"][0], part["commercial_partner_id"][0]}
            tm = list({p["product_tmpl_id"][0] for p in pr.values()})
            refp = {}
            for si in (odoo.search_read("product.supplierinfo", [("product_tmpl_id", "in", tm), ("partner_id", "in", list(provs))],
                                        ["product_tmpl_id", "product_id", "product_code", "product_name"], company_ids=TODAS) if tm else []):
                if si.get("product_code"):
                    refp.setdefault(si["product_tmpl_id"][0], si["product_code"])
            st = {}
            for tit, emp, loc in STOCKS:
                try:
                    for q in odoo.search_read("stock.quant", [("product_id", "in", pids), ("location_id", "child_of", loc)], ["product_id", "quantity"],
                                              company_ids=[emp]):
                        st[(q["product_id"][0], tit)] = st.get((q["product_id"][0], tit), 0) + q["quantity"]
                except Exception:
                    pass
            emp = odoo.search_read("res.company", [("id", "=", EMPRESA)], ["name", "vat", "street", "zip", "city", "phone", "email"], company_ids=[EMPRESA])[0]
            return po, ls, pr, refp, st, emp, part
        po, ls, pr, refp, st, emp, part = guard(run)
        e = lambda x: _h.escape(str(x or ""))
        n = lambda x: (f"{x:g}".replace(".", ",") if isinstance(x, (int, float)) else "")
        filas = []
        for l in sorted(ls, key=lambda l: (pr.get(l["product_id"][0], {}).get("default_code") or "zzz", l["name"])):
            p = pr.get(l["product_id"][0], {})
            desc = _re.sub(r"^\[[^\]]*\]\s*", "", p.get("name") or l["name"])
            stock = "".join(f'<td class="n s{" sep" if k == 0 else ""}">{n(round(st.get((l["product_id"][0], t), 0), 2))}</td>' for k, (t, _, _) in enumerate(STOCKS))
            filas.append(f'<tr><td>{e(p.get("default_code"))}</td><td>{e(refp.get((p.get("product_tmpl_id") or [0])[0]) or p.get("x_studio_manufacturer_ref"))}</td><td>{e(desc)}</td>'
                         f'<td class="n"><b>{n(l["product_qty"])}</b></td><td>{e((l.get(uom()) or [0, ""])[1])}</td>{stock}</tr>')
        fecha = (po.get("date_approve") or po.get("date_order") or "")[:10]
        fecha = "/".join(reversed(fecha.split("-"))) if fecha else ""
        cab_st = "".join(f'<th class="n s">{t}</th>' for t, _, _ in STOCKS)
        dir_ = ", ".join(x for x in [emp.get("street"), " ".join(x for x in [emp.get("zip"), emp.get("city")] if x)] if x)
        page = f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Pedido {e(po['name'])} · {e(po['partner_id'][1])}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font:13px/1.35 -apple-system,Segoe UI,Arial,sans-serif;color:#222;background:#fff;margin:24px}}
.bar{{display:flex;gap:10px;align-items:center;margin-bottom:18px;flex-wrap:wrap}} .bar button{{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid #6b3fa0;background:#6b3fa0;color:#fff;cursor:pointer}}
.bar button.b2{{background:#fff;color:#6b3fa0}} .bar label{{margin-left:6px}}
h1{{font-size:20px;margin:0 0 2px;color:#6b3fa0}} .top{{display:flex;justify-content:space-between;gap:20px;border-bottom:3px solid #f08a24;padding-bottom:10px;margin-bottom:14px}}
.muted{{color:#666}} table{{width:100%;border-collapse:collapse}} th,td{{border-bottom:1px solid #ddd;padding:5px 6px;text-align:left;vertical-align:top}}
th{{background:#f3eef9;font-size:12px}} .n{{text-align:right;white-space:nowrap}} .s{{color:#555;background:#fafafa}} th.s{{background:#eee}}
.sep{{border-left:2px solid #999}} .pie{{margin-top:16px;font-size:12px}}
body.sin .s{{display:none}}
@media print{{.bar{{display:none}} body{{margin:10mm}}}}
</style></head><body>
<div class="bar"><button onclick="document.body.classList.add('sin');print()">🖨 Imprimir para el proveedor</button>
<button class="b2" onclick="document.body.classList.remove('sin');print()">🖨 Imprimir con stock</button>
<label><input type="checkbox" checked onchange="document.body.classList.toggle('sin',!this.checked)"> ver stock de almacén y tiendas</label></div>
<div class="top"><div><h1>Pedido de compra {e(po['name'])}</h1><div class="muted">Fecha: {fecha}</div>
<div style="margin-top:8px"><b>Proveedor:</b> {e(po['partner_id'][1])}<br><span class="muted">{e(part.get('email'))} {e(part.get('phone'))}</span></div></div>
<div style="text-align:right"><b>{e(emp['name'])}</b><br>{e(emp.get('vat'))}<br>{e(dir_)}<br>{e(emp.get('phone'))} {e(emp.get('email'))}<br><span class="muted">Folder Castellón</span></div></div>
<table><thead><tr><th>Ref. Folder</th><th>Ref. fabricante</th><th>Descripción</th><th class="n">Cantidad</th><th>Ud.</th>{cab_st.replace('<th class="n s">', '<th class="n s sep">', 1)}</tr></thead>
<tbody>{''.join(filas)}</tbody></table>
<p class="pie">{len(filas)} líneas. Entregar en: {e(emp['name'])}, {e(dir_)}. Por favor, indiquen nuestro nº de pedido <b>{e(po['name'])}</b> en el albarán.</p>
<p class="pie s muted">Stock a día de hoy (no sale al imprimir para el proveedor).</p>
</body></html>"""
        return HTMLResponse(page)

    @app.post("/api/tablero/ficha")
    async def ficha(request: Request):
        """{line_id}: añade el proveedor del pedido a la ficha del artículo (pestaña Compra de Odoo) con el precio de esa línea."""
        ctx(request)
        b = await request.json()
        lid = int(b.get("line_id") or 0)

        def run():
            ls = odoo.search_read("purchase.order.line", [("id", "=", lid)], ["product_id", "price_unit", "partner_id", "order_id"], company_ids=[EMPRESA])
            if not ls:
                raise HTTPException(404, "No encuentro la línea")
            l = ls[0]
            tm = odoo.search_read("product.product", [("id", "=", l["product_id"][0])], ["product_tmpl_id"], company_ids=[EMPRESA])[0]["product_tmpl_id"][0]
            part = l["partner_id"][0]
            # se busca en todas las empresas: si ya existe (aunque sea de otra empresa) se actualiza, no se duplica
            ya = odoo.search_read("product.supplierinfo", [("product_tmpl_id", "=", tm), ("partner_id", "=", part)], ["id"], company_ids=TODAS, limit=1,
                                  order="company_id desc")
            if ya:
                odoo.call("product.supplierinfo", "write", [EMPRESA], ids=[ya[0]["id"]], vals={"price": l["price_unit"]})
            else:
                odoo.call("product.supplierinfo", "create", [EMPRESA], vals_list=[{"partner_id": part, "product_tmpl_id": tm, "price": l["price_unit"],
                                                                                    "min_qty": 0, "company_id": EMPRESA}])
            return {"ok": True, "proveedor": l["partner_id"][1]}
        return guard(run)

    @app.post("/api/tablero/ocultar")
    async def ocultar(request: Request):
        """Quita del tablero un pedido que no es de material (p. ej. un servicio de imprenta de INGRAF). No toca Odoo."""
        ctx(request)
        b = await request.json()
        po = int(b.get("po_id") or 0)
        if not po:
            raise HTTPException(400, "Falta el pedido")
        with lock:
            oc = ocultos()
            oc.add(po)
            oc_file.write_text(_json.dumps(sorted(oc)))
        return {"ok": True}

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
