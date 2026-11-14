"""Apartado de cada tienda: lo que ha pedido al almacén central (reposiciones desde el botón «Reposición» del TPV)
y los encargos de clientes hechos en la tienda, hasta que el almacén los sirve. Y el acceso a su TPV.
"""
import datetime as dt

from fastapi import Request, HTTPException

TARJETA = "TARJETERO"   # diario del datáfono (TPV banco)

TODAS = [1, 2, 3, 4, 5]
# código de tienda -> datos en Odoo
TIENDAS = {
    "T1": {"nombre": "Tienda 1", "empresa": 1, "pos": 2, "almacen": 2, "usuario_odoo": 11, "reposicion": ["Tienda 1"], "caja": "Efectivo Tienda 1"},
    "T2": {"nombre": "Tienda 2", "empresa": 1, "pos": 4, "almacen": 3, "usuario_odoo": 12, "reposicion": ["Tienda 2"], "caja": "Efectivo T2"},
    "T3": {"nombre": "Tienda Logipaper", "empresa": 4, "pos": 5, "almacen": 4, "usuario_odoo": None, "reposicion": ["Tienda 3", "LOGIPAPER", "Logipaper"], "caja": "Efectivo Tienda 3"},
}


def setup(app, *, odoo, current_user, scope, guard, odoo_url, tienda_de):

    def ctx(request, t):
        u = current_user(request)
        propia = tienda_de(u)
        t = propia or (t or "T1").upper()
        if t not in TIENDAS:
            raise HTTPException(400, "Tienda no válida")
        return u, t

    def plazo(s):
        """Contado = se cobra en la tienda al entregar. Si el cliente tiene giro / fin de mes, la tienda solo sirve."""
        n = (s.get("payment_term_id") or [0, ""])[1] or ""
        return {"plazo": n or "Contado", "contado": (not n) or ("contado" in n.lower())}

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
            dom = [("state", "=", "sale"), ("delivery_status", "in", ["pending", "partial"]), ("company_id", "=", T["empresa"])]
            dom += ([("user_id", "=", T["usuario_odoo"])] if T["usuario_odoo"] else [("warehouse_id", "=", T["almacen"])])
            so = odoo.search_read("sale.order", dom, ["name", "partner_id", "date_order", "amount_total", "client_order_ref", "order_line", "payment_term_id"],
                                  company_ids=[T["empresa"]], order="date_order desc", limit=200)
            lids = [i for s in so for i in s["order_line"]]
            sl = odoo.search_read("sale.order.line", [("id", "in", lids), ("display_type", "=", False)], ["order_id", "product_id", "name", "product_uom_qty", "qty_delivered"],
                                  company_ids=[T["empresa"]]) if lids else []
            por = {}
            for l in sl:
                por.setdefault(l["order_id"][0], []).append({"articulo": (l.get("product_id") or [0, l["name"]])[1], "pedido": l["product_uom_qty"],
                                                            "servido": l["qty_delivered"], "pendiente": max(0, l["product_uom_qty"] - l["qty_delivered"])})
            propias = {c["partner_id"][0] for c in odoo.search_read("res.company", [], ["partner_id"], company_ids=TODAS)}
            so = [s for s in so if s["partner_id"][0] not in propias]   # fuera los pedidos entre nuestras empresas
            enc = [{"id": s["id"], "ref": s["name"], "cliente": s["partner_id"][1], "fecha": (s.get("date_order") or "")[:10], "total": s["amount_total"],
                    "ref_cliente": s.get("client_order_ref") or "", "lineas": por.get(s["id"], []), **plazo(s)} for s in so]
            # servidos (albarán ya sacado) y todavía sin cobrar: se cobran desde aquí, sin pasar por el TPV
            desde = (dt.date.today() - dt.timedelta(days=90)).isoformat()
            dom2 = [("state", "=", "sale"), ("delivery_status", "=", "full"), ("company_id", "=", T["empresa"]), ("date_order", ">=", desde)]
            dom2 += ([("user_id", "=", T["usuario_odoo"])] if T["usuario_odoo"] else [("warehouse_id", "=", T["almacen"])])
            sv = odoo.search_read("sale.order", dom2, ["name", "partner_id", "date_order", "amount_total", "invoice_status", "invoice_ids", "client_order_ref", "payment_term_id"],
                                  company_ids=[T["empresa"]], order="date_order desc", limit=200)
            sv = [x for x in sv if x["partner_id"][0] not in propias]
            iids = [i for x in sv for i in x["invoice_ids"]]
            invs = {i["id"]: i for i in odoo.search_read("account.move", [("id", "in", iids)], ["name", "state", "payment_state", "amount_residual", "move_type"],
                                                           company_ids=[T["empresa"]])} if iids else {}
            cobrar = []
            for x in sv:
                fs = [invs[i] for i in x["invoice_ids"] if i in invs and invs[i]["state"] != "cancel" and invs[i]["move_type"] == "out_invoice"]
                pend = sum(f["amount_residual"] for f in fs if f["state"] == "posted" and f["payment_state"] not in ("paid", "in_payment", "reversed"))
                sin_fact = x["invoice_status"] == "to invoice"
                if sin_fact or pend > 0.009:
                    cobrar.append({"id": x["id"], "ref": x["name"], "cliente": x["partner_id"][1], "fecha": (x.get("date_order") or "")[:10],
                                   "importe": round(pend if fs and not sin_fact else x["amount_total"], 2), "factura": ", ".join(f["name"] for f in fs if f["name"] != "/"),
                                   "ref_cliente": x.get("client_order_ref") or "", **plazo(x)})
            return {"tienda": t, "nombre": T["nombre"], "por_cobrar": cobrar, "fija": bool(tienda_de(u)), "tpv": f"{odoo_url}/pos/ui?config_id={T['pos']}",
                    "reposiciones": rep, "encargos": enc, "tiendas": [{"codigo": k, "nombre": v["nombre"]} for k, v in TIENDAS.items()]}
        return guard(run)

    def diario(nombre, empresa):
        j = odoo.search_read("account.journal", [("name", "=", nombre), ("company_id", "=", empresa)], ["id"], company_ids=[empresa], limit=1)
        if not j:
            raise HTTPException(400, f"No encuentro el diario «{nombre}» en Odoo")
        return j[0]["id"]

    @app.post("/api/tienda/preparar_cobro")
    async def preparar_cobro(request: Request):
        """{so_id}: factura lo servido (si falta) para que la tienda lo cobre en el TPV con «Liquidar deuda».
        Así el dinero entra en la sesión de caja del TPV (cuadra el arqueo) y no se vuelve a mover material."""
        b = await request.json()
        u, t = ctx(request, b.get("t") or "")
        T = TIENDAS[t]
        so_id = int(b.get("so_id") or 0)
        E = T["empresa"]

        def run():
            so = odoo.search_read("sale.order", [("id", "=", so_id), ("company_id", "=", E)], ["name", "invoice_status", "user_id", "warehouse_id", "partner_id"],
                                  company_ids=[E])
            if not so:
                raise HTTPException(404, "No encuentro el pedido")
            so = so[0]
            if (T["usuario_odoo"] and (so.get("user_id") or [0])[0] != T["usuario_odoo"]) or (not T["usuario_odoo"] and (so.get("warehouse_id") or [0])[0] != T["almacen"]):
                raise HTTPException(403, "Ese pedido no es de esta tienda")
            ctx_so = {"active_model": "sale.order", "active_ids": [so_id], "active_id": so_id}
            if so["invoice_status"] == "to invoice":
                w = odoo.call("sale.advance.payment.inv", "create", [E], vals_list=[{"advance_payment_method": "delivered", "sale_order_ids": [[6, 0, [so_id]]]}], context=ctx_so)
                w = w[0] if isinstance(w, list) else w
                odoo.call("sale.advance.payment.inv", "create_invoices", [E], ids=[w], context=ctx_so)
            fs = odoo.search_read("account.move", [("invoice_origin", "=", so["name"]), ("move_type", "=", "out_invoice"), ("state", "!=", "cancel"), ("company_id", "=", E)],
                                  ["name", "state"], company_ids=[E])
            borr = [f["id"] for f in fs if f["state"] == "draft"]
            if borr:
                odoo.call("account.move", "action_post", [E], ids=borr)
            fs = odoo.search_read("account.move", [("invoice_origin", "=", so["name"]), ("move_type", "=", "out_invoice"), ("state", "=", "posted"), ("company_id", "=", E)],
                                  ["name", "amount_residual", "payment_state"], company_ids=[E])
            pend = [f for f in fs if f["payment_state"] not in ("paid", "in_payment", "reversed") and f["amount_residual"] > 0.009]
            return {"ok": True, "cliente": so["partner_id"][1], "facturas": [f["name"] for f in pend], "importe": round(sum(f["amount_residual"] for f in pend), 2)}
        return guard(run)
