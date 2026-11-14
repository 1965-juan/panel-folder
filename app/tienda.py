"""Apartado de cada tienda: lo que ha pedido al almacén central (reposiciones desde el botón «Reposición» del TPV)
y los encargos de clientes hechos en la tienda, hasta que el almacén los sirve. Y el acceso a su TPV.
"""
from fastapi import Request, HTTPException

TODAS = [1, 2, 3, 4, 5]
# código de tienda -> datos en Odoo
TIENDAS = {
    "T1": {"nombre": "Tienda 1", "empresa": 1, "pos": 2, "almacen": 2, "usuario_odoo": 11, "reposicion": ["Tienda 1"]},
    "T2": {"nombre": "Tienda 2", "empresa": 1, "pos": 4, "almacen": 3, "usuario_odoo": 12, "reposicion": ["Tienda 2"]},
    "T3": {"nombre": "Tienda Logipaper", "empresa": 4, "pos": 5, "almacen": 4, "usuario_odoo": None, "reposicion": ["Tienda 3", "LOGIPAPER", "Logipaper"]},
}


def setup(app, *, odoo, current_user, scope, guard, odoo_url, tienda_de):

    def ctx(request, t):
        u = current_user(request)
        propia = tienda_de(u)
        t = propia or (t or "T1").upper()
        if t not in TIENDAS:
            raise HTTPException(400, "Tienda no válida")
        return u, t

    @app.get("/api/tienda/datos")
    def datos(request: Request, t: str = ""):
        u, t = ctx(request, t)
        T = TIENDAS[t]

        def run():
            # reposiciones pedidas desde el TPV (las mismas que ve el almacén en el Panel AC → Tiendas)
            rep = []
            try:
                d = odoo.call_kw("sale.order", "get_panel_data", [], company_ids=[1, 4])
                for p in d.get("reposicion", []):
                    if any(n.lower() == (p.get("store_name") or "").lower() for n in T["reposicion"]):
                        ls = [{"articulo": l["product_name"], "pedido": l["qty_ordered"], "servido": l.get("qty_delivered") or 0,
                               "pendiente": l["qty_pending"], "estado": l.get("badge_label") or ""} for l in p["lines"] if (l.get("qty_pending") or 0) > 0]
                        if ls:
                            rep.append({"id": p["picking_id"], "ref": p["picking_name"], "fecha": p["date"], "lineas": ls})
            except Exception as e:
                rep = [{"error": str(e)[:200]}]
            # encargos de clientes hechos en la tienda, hasta que el almacén saca el albarán
            dom = [("state", "=", "sale"), ("delivery_status", "!=", "full"), ("company_id", "=", T["empresa"])]
            dom += (["|", ("user_id", "=", T["usuario_odoo"]), ("warehouse_id", "=", T["almacen"])] if T["usuario_odoo"] else [("warehouse_id", "=", T["almacen"])])
            so = odoo.search_read("sale.order", dom, ["name", "partner_id", "date_order", "amount_total", "client_order_ref", "order_line"],
                                  company_ids=[T["empresa"]], order="date_order desc", limit=200)
            lids = [i for s in so for i in s["order_line"]]
            sl = odoo.search_read("sale.order.line", [("id", "in", lids), ("display_type", "=", False)], ["order_id", "product_id", "name", "product_uom_qty", "qty_delivered"],
                                  company_ids=[T["empresa"]]) if lids else []
            por = {}
            for l in sl:
                por.setdefault(l["order_id"][0], []).append({"articulo": (l.get("product_id") or [0, l["name"]])[1], "pedido": l["product_uom_qty"],
                                                            "servido": l["qty_delivered"], "pendiente": max(0, l["product_uom_qty"] - l["qty_delivered"])})
            enc = [{"id": s["id"], "ref": s["name"], "cliente": s["partner_id"][1], "fecha": (s.get("date_order") or "")[:10], "total": s["amount_total"],
                    "ref_cliente": s.get("client_order_ref") or "", "lineas": por.get(s["id"], [])} for s in so]
            return {"tienda": t, "nombre": T["nombre"], "fija": bool(tienda_de(u)), "tpv": f"{odoo_url}/pos/ui?config_id={T['pos']}",
                    "reposiciones": rep, "encargos": enc, "tiendas": [{"codigo": k, "nombre": v["nombre"]} for k, v in TIENDAS.items()]}
        return guard(run)
