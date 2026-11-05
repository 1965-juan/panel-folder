"""Panel AC (almacén): pedidos de clientes y reposición de tiendas, con stock por ubicación.

Usa las mismas funciones que el Panel AC que Nextdoo instaló en Odoo (módulo folder_AC_panel),
así que validar, cambiar cantidades o crear traspasos hace exactamente lo mismo que allí.
Novedades: borrar y añadir líneas en pedidos de clientes y tiendas, filtro de pedidos completos,
y ver los datos del cliente sin cuenta que la tienda apunta en "Referencia del cliente".
"""
from fastapi import Request, HTTPException


def setup(app, *, odoo, current_user, scope, guard, odoo_url):

    def ctx(request, company):
        u = current_user(request)
        cids, _ = scope(u, company)
        return u, cids

    @app.get("/api/ac/datos")
    def ac_datos(request: Request, company: str = "all"):
        _, cids = ctx(request, company)

        def run():
            d = odoo.call_kw("sale.order", "get_panel_data", [], company_ids=cids)
            ids = [o["order_id"] for o in d.get("orders", [])]
            extra = {}
            if ids:
                for r in odoo.search_read("sale.order", [("id", "in", ids)], ["client_order_ref", "note"], company_ids=cids):
                    extra[r["id"]] = r
            for o in d.get("orders", []):
                e = extra.get(o["order_id"], {})
                o["ref_cliente"] = e.get("client_order_ref") or ""
            d["odoo_url"] = odoo_url
            return d
        return guard(run)

    async def body(request):
        try:
            return await request.json()
        except Exception:
            return {}

    @app.post("/api/ac/cantidad")
    async def ac_cantidad(request: Request, company: str = "all"):
        _, cids = ctx(request, company)
        b = await body(request)
        qty = float(b.get("qty", 0))
        if qty <= 0:
            raise HTTPException(400, "Para quitar la línea usa la papelera")
        metodo = "update_reposicion_qty" if b.get("tipo") == "reposicion" else "update_order_line_qty"
        return guard(lambda: {"ok": odoo.call_kw("sale.order", metodo, [int(b["id"]), qty], company_ids=cids)})

    @app.post("/api/ac/borrar")
    async def ac_borrar(request: Request, company: str = "all"):
        _, cids = ctx(request, company)
        b = await body(request)
        lid = int(b["id"])

        def run():
            if b.get("tipo") == "reposicion":
                return {"ok": odoo.call_kw("sale.order", "cancel_reposicion_move", [lid], company_ids=cids)}
            # pedido de cliente: si el pedido no está confirmado se borra la línea; si lo está, se deja a 0
            ln = odoo.search_read("sale.order.line", [("id", "=", lid)], ["state", "qty_delivered"], company_ids=cids)
            if not ln:
                raise HTTPException(404, "Línea no encontrada")
            if ln[0].get("qty_delivered"):
                raise HTTPException(400, "Esta línea ya tiene algo servido: cambia la cantidad a lo servido en lugar de borrarla")
            if ln[0].get("state") in ("draft", "sent"):
                odoo.call("sale.order.line", "unlink", cids, ids=[lid])
            else:
                odoo.call("sale.order.line", "write", cids, ids=[lid], vals={"product_uom_qty": 0})
            return {"ok": True}
        return guard(run)

    @app.get("/api/ac/productos")
    def ac_productos(request: Request, q: str = "", company: str = "all"):
        _, cids = ctx(request, company)
        q = q.strip()
        if len(q) < 2:
            return []
        dom = ["&", ("sale_ok", "=", True), "|", "|", ("name", "ilike", q), ("default_code", "ilike", q), ("barcode", "=", q)]
        return guard(lambda: odoo.search_read("product.product", dom, ["display_name", "default_code", "lst_price"],
                                              company_ids=cids, limit=15))

    @app.post("/api/ac/anadir")
    async def ac_anadir(request: Request, company: str = "all"):
        _, cids = ctx(request, company)
        b = await body(request)
        pid, qty = int(b["product_id"]), float(b.get("qty") or 1)

        def run():
            if b.get("tipo") == "reposicion":
                pk = odoo.search_read("stock.picking", [("id", "=", int(b["id"]))],
                                      ["location_id", "location_dest_id", "company_id", "state"], company_ids=cids)
                if not pk:
                    raise HTTPException(404, "Pedido de tienda no encontrado")
                pk = pk[0]
                pr = odoo.search_read("product.product", [("id", "=", pid)], ["uom_id", "display_name"], company_ids=cids)[0]
                mid = odoo.call("stock.move", "create", cids, vals_list=[{
                    "picking_id": pk["id"], "product_id": pid, "product_uom_qty": qty, "product_uom": pr["uom_id"][0],
                    "location_id": pk["location_id"][0], "location_dest_id": pk["location_dest_id"][0],
                    "company_id": pk["company_id"][0]}])
                if pk["state"] != "draft":
                    # confirma la línea nueva para que aparezca en el panel como el resto
                    odoo.call("stock.picking", "action_confirm", cids, ids=[pk["id"]])
                return {"ok": True}
            odoo.call("sale.order.line", "create", cids, vals_list=[{
                "order_id": int(b["id"]), "product_id": pid, "product_uom_qty": qty}])
            return {"ok": True}
        return guard(run)

    @app.post("/api/ac/validar")
    async def ac_validar(request: Request, company: str = "all"):
        _, cids = ctx(request, company)
        b = await body(request)
        out = {"picking_names": [], "documents": []}

        def run():
            for tipo, metodo, clave in (("lineas", "validate_panel_selection", "order_line_id"),
                                        ("reposicion", "validate_reposicion_selection", "move_id")):
                sel = [{clave: int(x["id"]), "transfers": {str(k): float(v) for k, v in x["transfers"].items() if float(v) > 0}}
                       for x in b.get(tipo, []) if any(float(v) > 0 for v in x.get("transfers", {}).values())]
                if sel:
                    r = odoo.call_kw("sale.order", metodo, [sel], company_ids=cids) or {}
                    if isinstance(r, dict):
                        out["picking_names"] += r.get("picking_names") or []
                        out["documents"] += r.get("documents") or []
            return out
        return guard(run)
