"""Nuevo pedido (presupuesto o pedido de cliente hecho desde el panel, como en Odoo) y buscador de Artículos
(stock por tienda, precios y proveedores). Pensado para almacén y compras."""
from fastapi import Request, HTTPException

# ubicaciones de stock que se enseñan: (nombre, empresa, ubicación)
STOCKS = [("Almacén", 1, 8), ("Tienda 1", 1, 34), ("Tienda 2", 1, 40), ("T. Logipaper", 4, 46)]


def setup(app, *, odoo, current_user, scope, guard, odoo_url, allowed_companies, nombre_usuario):

    def empresas(u):
        return [c["id"] for c in allowed_companies(u)]

    @app.get("/api/articulos")
    def articulos(request: Request, q: str = "", company: str = "all"):
        u = current_user(request)
        q = q.strip()
        if len(q) < 2:
            return {"articulos": [], "odoo_url": odoo_url}

        def run():
            cids, _ = scope(u, company)
            pal = [w for w in q.split() if w]
            dom = [("active", "=", True)]
            for w in pal:   # cada palabra en el nombre o en alguna referencia
                dom += ["|", "|", "|", ("name", "ilike", w), ("default_code", "ilike", w), ("barcode", "ilike", w), ("x_studio_manufacturer_ref", "ilike", w)]
            ps = odoo.search_read("product.product", dom, ["default_code", "name", "lst_price", "standard_price", "barcode", "x_studio_manufacturer_ref",
                                                          "product_tmpl_id", "uom_id", "sale_ok", "purchase_ok"], cids, limit=40, order="default_code,name")
            if not ps:
                return {"articulos": [], "odoo_url": odoo_url}
            ids = [p["id"] for p in ps]
            stock = {}
            for nom, emp, loc in STOCKS:
                if emp not in empresas(u) and emp != 1:
                    continue
                try:
                    for qq in odoo.search_read("stock.quant", [("product_id", "in", ids), ("location_id", "child_of", loc)],
                                               ["product_id", "quantity", "reserved_quantity"], [emp] + [c for c in cids if c != emp], limit=5000):
                        s = stock.setdefault(qq["product_id"][0], {}).setdefault(nom, [0, 0])
                        s[0] += qq["quantity"]; s[1] += qq.get("reserved_quantity") or 0
                except Exception:
                    pass
            tm = list({p["product_tmpl_id"][0] for p in ps})
            prov = {}
            try:
                for s in odoo.search_read("product.supplierinfo", [("product_tmpl_id", "in", tm)], ["product_tmpl_id", "partner_id", "price", "product_code"],
                                          cids, limit=2000, order="price"):
                    lst = prov.setdefault(s["product_tmpl_id"][0], [])
                    if not any(x["proveedor"] == s["partner_id"][1] for x in lst):
                        lst.append({"proveedor": s["partner_id"][1], "precio": s["price"], "ref": s.get("product_code") or ""})
            except Exception:
                pass
            out = []
            for p in ps:
                st = stock.get(p["id"], {})
                out.append({"id": p["id"], "tmpl_id": p["product_tmpl_id"][0], "ref": p.get("default_code") or "",
                            "ref_fab": p.get("x_studio_manufacturer_ref") or next((x["ref"] for x in prov.get(p["product_tmpl_id"][0], []) if x["ref"]), ""),
                            "nombre": p["name"], "pvp": p.get("lst_price") or 0, "coste": p.get("standard_price") or 0,
                            "ud": (p.get("uom_id") or [0, ""])[1], "ean": p.get("barcode") or "", "venta": p.get("sale_ok"), "compra": p.get("purchase_ok"),
                            "stock": [{"donde": nom, "hay": round(st.get(nom, [0, 0])[0], 2), "reservado": round(st.get(nom, [0, 0])[1], 2)}
                                      for nom, emp, loc in STOCKS if emp in empresas(u) or emp == 1],
                            "proveedores": prov.get(p["product_tmpl_id"][0], [])[:4]})
            return {"articulos": out, "odoo_url": odoo_url}
        return guard(run)

    @app.get("/api/pedidos/clientes")
    def clientes(request: Request, q: str = ""):
        u = current_user(request)
        q = q.strip()
        if len(q) < 2:
            return []

        def run():
            cids = empresas(u)
            dom = [("active", "=", True), "|", "|", "|", ("name", "ilike", q), ("vat", "ilike", q), ("ref", "ilike", q), ("email", "ilike", q)]
            ps = odoo.search_read("res.partner", dom, ["display_name", "email", "vat", "city", "is_company", "parent_id"], cids, limit=25, order="is_company desc,name")
            return [{"id": p["id"], "nombre": p["display_name"], "email": p.get("email") or "", "nif": p.get("vat") or "", "ciudad": p.get("city") or ""} for p in ps]
        return guard(run)

    @app.post("/api/pedidos/crear")
    async def crear(request: Request):
        """{empresa, partner_id, lineas:[{product_id, cantidad, precio?}], ref, nota, confirmar}: crea el presupuesto en Odoo
        (o el pedido, si confirmar). Los precios los pone Odoo con la tarifa del cliente salvo que se indique uno."""
        u = current_user(request)
        b = await request.json()

        def run():
            emp = int(b.get("empresa") or 0)
            if emp not in empresas(u):
                raise HTTPException(403, "No puedes hacer pedidos en esa empresa")
            pid = int(b.get("partner_id") or 0)
            ls = [l for l in (b.get("lineas") or []) if int(l.get("product_id") or 0) and float(l.get("cantidad") or 0) > 0]
            if not pid:
                raise HTTPException(400, "Falta el cliente")
            if not ls:
                raise HTTPException(400, "El pedido no tiene artículos")
            lineas = []
            for l in ls:
                v = {"product_id": int(l["product_id"]), "product_uom_qty": float(l["cantidad"])}
                if l.get("precio") not in (None, ""):
                    v["price_unit"] = float(l["precio"])
                lineas.append([0, 0, v])
            vals = {"company_id": emp, "partner_id": pid, "order_line": lineas, "origin": f"Panel ({nombre_usuario(u)})"}
            if (b.get("ref") or "").strip():
                vals["client_order_ref"] = b["ref"].strip()
            if (b.get("nota") or "").strip():
                vals["note"] = b["nota"].strip()
            r = odoo.call("sale.order", "create", [emp], vals_list=[vals])
            so_id = r[0] if isinstance(r, list) else r
            if b.get("confirmar"):
                odoo.call("sale.order", "action_confirm", [emp], ids=[so_id])
            so = odoo.search_read("sale.order", [("id", "=", so_id)], ["name", "amount_untaxed", "amount_total", "state"], [emp])[0]
            ln = odoo.search_read("sale.order.line", [("order_id", "=", so_id)], ["product_id", "product_uom_qty", "price_unit", "discount", "price_subtotal"], [emp])
            return {"id": so_id, "nombre": so["name"], "estado": so["state"], "base": so["amount_untaxed"], "total": so["amount_total"], "cid": emp,
                    "lineas": [{"articulo": l["product_id"][1] if l.get("product_id") else "", "cantidad": l["product_uom_qty"], "precio": l["price_unit"],
                                "dto": l.get("discount") or 0, "subtotal": l["price_subtotal"]} for l in ln],
                    "odoo": f"{odoo_url}/odoo/sales/{so_id}"}
        return guard(run)
