"""Asistente general del panel (sobre todo para almacén): preguntar por stock, pedidos, compras
y hacer pequeños cambios en Odoo hablando, con confirmación antes de tocar nada.

Usa la misma clave de Claude que el resto del panel (datos/claude_clave.txt) y respeta las
empresas que puede ver cada usuario (Almacén solo DISTRIPLUS).
"""
import asyncio
import json
import os
import re
from pathlib import Path

import httpx
from fastapi import Request, HTTPException

ESCRITURA = {"pedir_a_proveedor", "cambiar_cantidad", "quitar_linea", "anadir_linea"}


def setup(app, *, data_dir: Path, odoo, current_user, scope, guard, odoo_url):
    clave_f = data_dir / "claude_clave.txt"
    modelo = os.environ.get("PANEL_MODEL") or os.environ.get("WA_MODEL") or "claude-sonnet-5-5"
    http = httpx.Client(timeout=180)

    def clave():
        if not clave_f.exists():
            return ""
        m = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", clave_f.read_text(errors="ignore"))
        return m[0] if m else ""

    HERR = [
        {"name": "buscar_articulos", "description": "Busca artículos por nombre, referencia o código de barras y da su stock en cada ubicación (AC, T1, T2…), lo reservado, precio y si hay pedido a proveedor.",
         "input_schema": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]}},
        {"name": "pedidos_pendientes", "description": "Pedidos de clientes y reposiciones de tiendas pendientes de servir (los del Panel AC). Filtra por cliente, tienda, pedido o artículo.",
         "input_schema": {"type": "object", "properties": {"filtro": {"type": "string"}}}},
        {"name": "ver_pedido", "description": "Detalle de un pedido de venta por número (S02799) o los últimos de un cliente: líneas, servido, facturado, estado.",
         "input_schema": {"type": "object", "properties": {"pedido": {"type": "string"}, "cliente": {"type": "string"}}}},
        {"name": "compras_de_articulo", "description": "Pedidos a proveedor en curso de un artículo (por id de producto) y sus proveedores con precio.",
         "input_schema": {"type": "object", "properties": {"product_id": {"type": "integer"}}, "required": ["product_id"]}},
        {"name": "pendiente_de_proveedores", "description": "Lo que está pendiente de llegar de proveedores (recepciones abiertas), filtrable por proveedor o artículo.",
         "input_schema": {"type": "object", "properties": {"filtro": {"type": "string"}}}},
        {"name": "pedir_a_proveedor", "description": "CAMBIA ODOO: pide un artículo a un proveedor (crea o suma al borrador de compra). Solo con confirmado=true tras el 'sí' del usuario.",
         "input_schema": {"type": "object", "properties": {"product_id": {"type": "integer"}, "partner_id": {"type": "integer"}, "cantidad": {"type": "number"},
                                                          "linea_pedido_id": {"type": "integer"}, "confirmado": {"type": "boolean"}}, "required": ["product_id", "partner_id", "cantidad", "confirmado"]}},
        {"name": "cambiar_cantidad", "description": "CAMBIA ODOO: cambia la cantidad pedida de una línea de pedido de cliente (line_id) o de reposición de tienda (move_id). Solo con confirmado=true.",
         "input_schema": {"type": "object", "properties": {"line_id": {"type": "integer"}, "move_id": {"type": "integer"}, "cantidad": {"type": "number"},
                                                          "confirmado": {"type": "boolean"}}, "required": ["cantidad", "confirmado"]}},
        {"name": "quitar_linea", "description": "CAMBIA ODOO: quita una línea de un pedido de cliente (line_id) o de una reposición de tienda (move_id). Solo con confirmado=true.",
         "input_schema": {"type": "object", "properties": {"line_id": {"type": "integer"}, "move_id": {"type": "integer"}, "confirmado": {"type": "boolean"}}, "required": ["confirmado"]}},
        {"name": "anadir_linea", "description": "CAMBIA ODOO: añade un artículo a un pedido de cliente (order_id). Solo con confirmado=true.",
         "input_schema": {"type": "object", "properties": {"order_id": {"type": "integer"}, "product_id": {"type": "integer"}, "cantidad": {"type": "number"},
                                                          "confirmado": {"type": "boolean"}}, "required": ["order_id", "product_id", "cantidad", "confirmado"]}},
    ]

    def grupo():
        return [c["name"].split()[0].upper() for c in odoo.search_read("res.company", [], ["name"])]

    def es_grupo(nombre, claves):
        n = (nombre or "").upper()
        return any(k in n for k in claves)

    def ejecutar(nombre, a, cids, restringido=False):
        oculta = grupo() if restringido else []
        if nombre in ESCRITURA and not a.get("confirmado"):
            return "NO HECHO: primero explica al usuario exactamente qué vas a cambiar y espera a que diga que sí."
        if nombre == "buscar_articulos":
            t = a["texto"].strip()
            dom = ["|", "|", ("name", "ilike", t), ("default_code", "ilike", t), ("barcode", "=", t)]
            ps = odoo.search_read("product.product", dom, ["display_name", "default_code", "barcode", "lst_price", "qty_available"], company_ids=cids, limit=12)
            if not ps:
                palabras = [w for w in t.split() if len(w) > 1]
                if len(palabras) > 1:
                    ps = odoo.search_read("product.product", [("name", "ilike", w) for w in palabras], ["display_name", "default_code", "barcode", "lst_price", "qty_available"],
                                          company_ids=cids, limit=12)
            if not ps:
                return "No hay artículos con ese texto"
            qs = odoo.search_read("stock.quant", [("product_id", "in", [p["id"] for p in ps]), ("location_id.usage", "=", "internal"), ("company_id", "in", cids)],
                                  ["product_id", "location_id", "quantity", "reserved_quantity"], company_ids=cids, limit=500)
            out = []
            for p in ps:
                st = {}
                for q in qs:
                    if q["product_id"][0] == p["id"] and q["quantity"]:
                        loc = q["location_id"][1].split("/")[0]
                        s = st.setdefault(loc, {"stock": 0, "reservado": 0})
                        s["stock"] += q["quantity"]; s["reservado"] += q["reserved_quantity"]
                out.append({"product_id": p["id"], "articulo": p["display_name"], "barras": p.get("barcode") or "", "precio": p["lst_price"],
                            "stock_por_ubicacion": st or "sin stock en ninguna ubicación"})
            return out
        if nombre == "pedidos_pendientes":
            d = odoo.call_kw("sale.order", "get_panel_data", [], company_ids=cids)
            f = (a.get("filtro") or "").lower()
            out = []
            for o in d.get("orders", []):
                if es_grupo(o["partner_name"], oculta):
                    continue
                for l in o["lines"]:
                    txt = f"{o['order_name']} {o['partner_name']} {l['product_name']} {l.get('product_code')}".lower()
                    if not f or f in txt:
                        out.append({"tipo": "cliente", "pedido": o["order_name"], "order_id": o["order_id"], "cliente": o["partner_name"], "fecha": o["date"],
                                    "line_id": l["line_id"], "articulo": l["product_name"], "product_id": l["product_id"], "pedido_uds": l["qty_ordered"],
                                    "pendiente": l["qty_pending"], "estado": l["badge_label"], "stock": l["stock_by_loc"]})
            for p in d.get("reposicion", []):
                for l in p["lines"]:
                    txt = f"{p['picking_name']} {p['store_name']} {l['product_name']} {l.get('product_code')}".lower()
                    if not f or f in txt:
                        out.append({"tipo": "tienda", "pedido": p["picking_name"], "tienda": p["store_name"], "fecha": p["date"], "move_id": l["move_id"],
                                    "articulo": l["product_name"], "product_id": l["product_id"], "pendiente": l["qty_pending"], "estado": l["badge_label"]})
            locs = {str(x["id"]): x["code"] for x in d.get("locations", [])}
            for x in out:
                if "stock" in x:
                    x["stock"] = {locs.get(k, k): v for k, v in x["stock"].items() if v}
            return out[:80] if out else "No hay nada pendiente con ese filtro"
        if nombre == "ver_pedido":
            if a.get("pedido"):
                dom = [("name", "ilike", a["pedido"])]
            elif a.get("cliente"):
                dom = [("partner_id.name", "ilike", a["cliente"])]
            else:
                return "Dime el número de pedido o el cliente"
            os_ = odoo.search_read("sale.order", dom, ["name", "partner_id", "date_order", "state", "delivery_status", "invoice_status", "amount_total", "client_order_ref"],
                                   company_ids=cids, limit=5, order="date_order desc")
            os_ = [o for o in os_ if not es_grupo((o.get("partner_id") or [0, ""])[1], oculta)]
            if not os_:
                return "No encuentro ese pedido"
            for o in os_:
                o["lineas"] = [{"line_id": l["id"], "articulo": l["product_id"][1] if l["product_id"] else l["name"], "pedido": l["product_uom_qty"],
                                "servido": l["qty_delivered"], "facturado": l["qty_invoiced"]}
                               for l in odoo.search_read("sale.order.line", [("order_id", "=", o["id"]), ("display_type", "=", False)],
                                                         ["product_id", "name", "product_uom_qty", "qty_delivered", "qty_invoiced"], company_ids=cids)]
                o["cliente"] = o.pop("partner_id")[1]
                o["enlace"] = f"{odoo_url}/odoo/sales/{o['id']}"
            return os_
        if nombre == "compras_de_articulo":
            return {"estado": odoo.call_kw("sale.order", "get_purchase_status", [a["product_id"]], company_ids=cids),
                    "proveedores": odoo.call_kw("sale.order", "get_supplier_info", [a["product_id"]], company_ids=cids)}
        if nombre == "pendiente_de_proveedores":
            pk = odoo.search_read("stock.picking", [("picking_type_code", "=", "incoming"), ("state", "in", ["assigned", "confirmed", "waiting"])],
                                  ["name", "partner_id", "origin", "scheduled_date", "move_ids"], company_ids=cids, limit=100)
            mids = [m for p in pk for m in p["move_ids"]]
            mv = odoo.search_read("stock.move", [("id", "in", mids)], ["picking_id", "product_id", "product_uom_qty"], company_ids=cids) if mids else []
            f = (a.get("filtro") or "").lower()
            out = []
            for p in pk:
                ls = [f"{m['product_id'][1]} x{m['product_uom_qty']:g}" for m in mv if m["picking_id"][0] == p["id"]]
                txt = f"{p['name']} {(p['partner_id'] or [0, ''])[1]} {p.get('origin')} {' '.join(ls)}".lower()
                if not f or f in txt:
                    out.append({"recepcion": p["name"], "proveedor": (p["partner_id"] or [0, ""])[1], "pedido": p.get("origin"),
                                "previsto": (p.get("scheduled_date") or "")[:10], "articulos": ls})
            return out or "No hay nada pendiente de llegar con ese filtro"
        if nombre == "pedir_a_proveedor":
            return odoo.call_kw("sale.order", "ac_create_purchase_order", [a["product_id"], a["partner_id"], a["cantidad"], a.get("linea_pedido_id") or False], company_ids=cids)
        if nombre == "cambiar_cantidad":
            if a.get("move_id"):
                return odoo.call_kw("sale.order", "update_reposicion_qty", [a["move_id"], a["cantidad"]], company_ids=cids)
            return odoo.call_kw("sale.order", "update_order_line_qty", [a["line_id"], a["cantidad"]], company_ids=cids)
        if nombre == "quitar_linea":
            if a.get("move_id"):
                return odoo.call_kw("sale.order", "cancel_reposicion_move", [a["move_id"]], company_ids=cids)
            ln = odoo.search_read("sale.order.line", [("id", "=", a["line_id"])], ["state", "qty_delivered"], company_ids=cids)
            if not ln:
                return "No existe esa línea"
            if ln[0]["qty_delivered"]:
                return "Esa línea ya tiene algo servido: solo se puede bajar la cantidad hasta lo servido"
            if ln[0]["state"] in ("draft", "sent"):
                odoo.call("sale.order.line", "unlink", cids, ids=[a["line_id"]])
            else:
                odoo.call("sale.order.line", "write", cids, ids=[a["line_id"]], vals={"product_uom_qty": 0})
            return "Quitada"
        if nombre == "anadir_linea":
            odoo.call("sale.order.line", "create", cids, vals_list=[{"order_id": a["order_id"], "product_id": a["product_id"], "product_uom_qty": a["cantidad"]}])
            return "Añadida"
        return "Herramienta desconocida"

    @app.post("/api/asistente")
    async def asistente(request: Request):
        u = current_user(request)
        if not clave():
            raise HTTPException(400, "Falta la clave de Claude en el NAS")
        cids, dom = scope(u, "all")
        restringido = bool(dom)
        b = await request.json()
        hist = [m for m in (b.get("historial") or [])[-14:] if m.get("role") in ("user", "assistant") and m.get("content")]
        while hist and hist[0]["role"] != "user":
            hist.pop(0)
        msgs = hist + [{"role": "user", "content": b.get("mensaje") or ""}]
        sistema = (f"Eres el asistente del almacén de Folder Castellón (papelería y material de oficina). Hablas con {u.capitalize()}, en español, "
                   "con frases cortas y claras, como un compañero. Usas las herramientas para consultar Odoo: stock por ubicación "
                   "(AC = Almacén Central, IMPT = imprenta, VP = Logipaper, T1/T2/T3 = tiendas), pedidos de clientes y reposiciones de tiendas "
                   "pendientes, pedidos a proveedor y lo que falta por llegar. No inventes datos: si no lo encuentras, dilo. "
                   "Para CAMBIAR algo en Odoo (pedir a proveedor, cambiar cantidades, quitar o añadir líneas): primero di exactamente qué vas a "
                   "hacer y pregunta '¿lo hago?'; solo cuando el usuario conteste que sí, llama a la herramienta con confirmado=true. "
                   "Si te piden algo que no puedes hacer (por ejemplo validar, devolver material o traspasos), explica cómo hacerlo en el "
                   "Panel AC o en Odoo y no lo intentes. Cuando des listas, usa viñetas cortas."
                   + (" Trabajas solo con DISTRIPLUS: no menciones nunca otras empresas ni ventas entre empresas." if restringido else ""))
        hechos = []

        def llamar():
            texto = ""
            for _ in range(10):
                r = http.post(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages", headers={
                    "x-api-key": clave(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": modelo, "max_tokens": 2500, "system": sistema, "tools": HERR, "messages": msgs})
                if r.status_code != 200:
                    raise HTTPException(502, f"Claude respondió {r.status_code}: {r.text[:300]}")
                res = r.json()
                bl = res.get("content", [])
                texto = "".join(x.get("text", "") for x in bl if x.get("type") == "text").strip() or texto
                usos = [x for x in bl if x.get("type") == "tool_use"]
                msgs.append({"role": "assistant", "content": bl})
                if not usos:
                    return texto
                outs = []
                for t in usos:
                    try:
                        out = ejecutar(t["name"], t.get("input") or {}, cids, restringido)
                        if t["name"] in ESCRITURA and (t.get("input") or {}).get("confirmado"):
                            hechos.append(t["name"])
                    except Exception as e:
                        out = f"Error: {e}"
                    outs.append({"type": "tool_result", "tool_use_id": t["id"], "content": json.dumps(out, ensure_ascii=False, default=str)[:30000]})
                msgs.append({"role": "user", "content": outs})
            return texto or "No he terminado, dime si sigo."

        texto = await asyncio.to_thread(llamar)
        return {"texto": texto, "cambios": bool(hechos)}
