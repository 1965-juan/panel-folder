"""Etiquetas de tienda (70 x 30 mm, 27 por A4) con precio con IVA, escalados, logo y código de barras.
Mismo formato que la página etiquetas_tiendas.html, pero con los datos sacados de Odoo:
- artículos marcados en Odoo (botón «🏷 Etiquetas en el Panel» en Inventario → Productos),
- buscador del panel,
- o pidiéndolo en lenguaje normal («todo lo que ha entrado de Depesche esta semana»).
"""
import datetime as dt
import json
import os
import re
from pathlib import Path

import httpx
from fastapi import Request, HTTPException

TARIFA_NOMBRE = "Folder Castellón"
EMPRESA = 1  # DISTRIPLUS: impuestos y tarifa de las tiendas


def setup(app, *, data_dir: Path, odoo, current_user, scope, guard, tienda_de=lambda u: None):
    cache = {}

    def tarifa():
        if "tarifa" not in cache:
            t = odoo.search_read("product.pricelist", [("name", "ilike", TARIFA_NOMBRE)], ["id"], company_ids=[EMPRESA], limit=1)
            cache["tarifa"] = t[0]["id"] if t else 16
        return cache["tarifa"]

    def impuestos(ids):
        falta = [i for i in ids if ("tax", i) not in cache]
        if falta:
            for t in odoo.search_read("account.tax", [("id", "in", falta)], ["amount", "amount_type", "price_include", "company_id"], company_ids=[1, 2, 3, 4, 5]):
                cache[("tax", t["id"])] = t
        return [cache.get(("tax", i)) for i in ids if cache.get(("tax", i))]

    def con_iva(precio, taxes):
        pct = sum(t["amount"] for t in taxes if t["amount_type"] == "percent" and not t.get("price_include") and t["company_id"][0] == EMPRESA)
        if not any(t["company_id"][0] == EMPRESA for t in taxes):
            pct = 21.0
        return round(precio * (1 + pct / 100), 2)

    def datos_etiquetas(pids):
        """Para cada product.product: nombre, ref, EAN, PVP con IVA y escalados con IVA (tarifa Folder Castellón)."""
        pids = [int(p) for p in dict.fromkeys(pids) if p]
        if not pids:
            return []
        ps = []
        for i in range(0, len(pids), 300):
            ps += odoo.search_read("product.product", [("id", "in", pids[i:i + 300])],
                                   ["name", "display_name", "default_code", "barcode", "lst_price", "taxes_id", "product_tmpl_id",
                                    "product_template_attribute_value_ids"], company_ids=[EMPRESA])
        tmpls = list({p["product_tmpl_id"][0] for p in ps})
        hoy = dt.date.today().isoformat()
        items = odoo.search_read("product.pricelist.item", [("pricelist_id", "=", tarifa()), ("min_quantity", ">", 1),
                                                             "|", ("product_id", "in", pids), ("product_tmpl_id", "in", tmpls)],
                                 ["product_id", "product_tmpl_id", "applied_on", "min_quantity", "compute_price", "fixed_price", "percent_price",
                                  "date_start", "date_end"], company_ids=[EMPRESA], limit=20000)
        por_var, por_tmpl = {}, {}
        for it in items:
            if (it.get("date_start") and str(it["date_start"])[:10] > hoy) or (it.get("date_end") and str(it["date_end"])[:10] < hoy):
                continue
            if it["applied_on"] == "0_product_variant" and it.get("product_id"):
                por_var.setdefault(it["product_id"][0], []).append(it)
            elif it["applied_on"] == "1_product" and it.get("product_tmpl_id"):
                por_tmpl.setdefault(it["product_tmpl_id"][0], []).append(it)
        tax_ids = list({t for p in ps for t in p.get("taxes_id") or []})
        impuestos(tax_ids)
        orden = {p: i for i, p in enumerate(pids)}
        out = []
        for p in sorted(ps, key=lambda x: orden.get(x["id"], 0)):
            taxes = impuestos(p.get("taxes_id") or [])
            base = p.get("lst_price") or 0
            esc = {}
            for it in por_tmpl.get(p["product_tmpl_id"][0], []) + por_var.get(p["id"], []):  # la variante manda sobre la plantilla
                if it["compute_price"] == "fixed":
                    pr = it["fixed_price"]
                elif it["compute_price"] == "percentage":
                    pr = base * (1 - (it.get("percent_price") or 0) / 100)
                else:
                    continue
                esc[it["min_quantity"]] = pr
            nombre = re.sub(r"^\[[^\]]*\]\s*", "", p.get("display_name") or p["name"])
            out.append({"id": p["id"], "nombre": nombre, "ref": p.get("default_code") or "", "cod": (p.get("barcode") or "").strip(),
                        "precio": con_iva(base, taxes), "base": round(base, 4),
                        "escalados": [{"desde": q, "precio": con_iva(v, taxes)} for q, v in sorted(esc.items()) if v and v < base][:3]})
        return out

    def ctx(request):
        u = current_user(request)
        cids, _ = scope(u, "all")
        return u, cids

    @app.get("/api/etiquetas/buscar")
    def buscar(request: Request, q: str = ""):
        _, cids = ctx(request)
        q = q.strip()
        if len(q) < 2:
            return []

        def run():
            dom = [("sale_ok", "=", True), "|", "|", ("name", "ilike", q), ("default_code", "ilike", q), ("barcode", "ilike", q)]
            return [{"id": p["id"], "nombre": re.sub(r"^\[[^\]]*\]\s*", "", p["display_name"]), "ref": p.get("default_code") or "", "cod": p.get("barcode") or ""}
                    for p in odoo.search_read("product.product", dom, ["display_name", "default_code", "barcode"], company_ids=[EMPRESA], limit=60)]
        return guard(run)

    @app.get("/api/etiquetas/catalogo")
    def catalogo(request: Request, q: str = "", categ: int = 0, stock: int = 1, t: str = ""):
        """Listado tipo inventario para marcar artículos: busca por palabras / ref / EAN / categoría y, si se pide, solo lo que hay en stock en la tienda."""
        u, cids = ctx(request)
        t = tienda_de(u) or (t or "").upper()

        def run():
            dom = [("sale_ok", "=", True)]
            for w in [w for w in q.split() if w][:5]:
                dom += ["|", "|", ("name", "ilike", w), ("default_code", "ilike", w), ("barcode", "=", w)]
            if categ:
                dom.append(("categ_id", "child_of", int(categ)))
            loc = {"T1": "T1/Stock", "T2": "T2/Stock", "T3": "VP/Stock"}.get(t)
            if stock and loc:
                qs = odoo.search_read("stock.quant", [("location_id.complete_name", "=", loc), ("quantity", ">", 0)], ["product_id", "quantity"],
                                      company_ids=[1, 2, 3, 4, 5], limit=20000)
                hay = {}
                for x in qs:
                    hay[x["product_id"][0]] = hay.get(x["product_id"][0], 0) + x["quantity"]
                dom.append(("id", "in", list(hay) or [0]))
            else:
                hay = None
            ps = odoo.search_read("product.product", dom, ["display_name", "default_code", "barcode", "lst_price", "categ_id"], company_ids=[EMPRESA], limit=400, order="name")
            return {"articulos": [{"id": p["id"], "nombre": re.sub(r"^\[[^\]]*\]\s*", "", p["display_name"]), "ref": p.get("default_code") or "", "ean": p.get("barcode") or "",
                                   "categoria": (p.get("categ_id") or [0, ""])[1].split(" / ")[-1], "stock": round(hay.get(p["id"], 0), 2) if hay is not None else None}
                                  for p in ps], "tienda": t, "max": len(ps) >= 400}
        return guard(run)

    @app.get("/api/etiquetas/categorias")
    def categorias(request: Request):
        ctx(request)
        return guard(lambda: [{"id": c["id"], "nombre": c["complete_name"]} for c in odoo.search_read("product.category", [], ["complete_name"], company_ids=[EMPRESA], order="complete_name", limit=500)])

    @app.post("/api/etiquetas/datos")
    async def datos(request: Request):
        """{items: [{id, cant}]} o {plantillas: [ids]} (lo marcado en Odoo) -> datos de cada etiqueta."""
        ctx(request)
        b = await request.json()

        def run():
            cant = {int(x["id"]): x.get("cant", 1) for x in b.get("items") or []}
            pids = list(cant)
            if b.get("plantillas"):
                tp = [int(t) for t in b["plantillas"] if str(t).isdigit()]
                vs = odoo.search_read("product.product", [("product_tmpl_id", "in", tp)], ["id"], company_ids=[EMPRESA], limit=3000) if tp else []
                pids += [v["id"] for v in vs if v["id"] not in cant]
            if b.get("productos"):
                pids += [int(x) for x in b["productos"] if str(x).isdigit() and int(x) not in cant]
            out = datos_etiquetas(pids)
            for e in out:
                e["cant"] = cant.get(e["id"], 1)
            return out
        return guard(run)

    # ---------- por tienda: solo lo que cada tienda tiene en stock, 1 etiqueta por artículo ----------
    TIENDAS = {"T1": "T1/Stock", "T2": "T2/Stock", "T3": "VP/Stock"}  # la Tienda 3 es el stock VP de Logipaper

    def por_tienda(pids):
        pids = [int(p) for p in dict.fromkeys(pids) if p]
        q = odoo.search_read("stock.quant", [("product_id", "in", pids), ("location_id.usage", "=", "internal"), ("quantity", ">", 0)],
                             ["product_id", "location_id", "quantity"], company_ids=[1, 2, 3, 4, 5], limit=20000) if pids else []
        est = {t: set() for t in TIENDAS}
        for x in q:
            for t, loc in TIENDAS.items():
                if x["location_id"][1] == loc:
                    est[t].add(x["product_id"][0])
        datos = {d["id"]: d for d in datos_etiquetas(pids)}
        con_precio = [d for d in datos.values() if d["precio"] > 0]
        orden = lambda L: sorted(L, key=lambda d: d["nombre"].lower())
        out = {t: orden([dict(d, cant=1) for d in con_precio if d["id"] in est[t]]) for t in TIENDAS}
        return {"tiendas": out, "todos": orden([dict(d, cant=1) for d in con_precio]),
                "sin_precio": sorted(d["nombre"] for d in datos.values() if d["precio"] <= 0),
                "sin_stock": sorted(d["nombre"] for d in con_precio if not any(d["id"] in est[t] for t in TIENDAS))}

    @app.post("/api/etiquetas/tiendas")
    async def tiendas(request: Request):
        """{productos:[ids]} o {plantillas:[ids]} o {entradas:[picking ids]} -> etiquetas separadas por tienda (según su stock)."""
        _u, cids = ctx(request)
        b = await request.json()

        def run():
            pids = [int(x) for x in b.get("productos") or [] if str(x).isdigit()]
            if b.get("plantillas"):
                pids += [v["id"] for v in odoo.search_read("product.product", [("product_tmpl_id", "in", [int(t) for t in b["plantillas"]])], ["id"], company_ids=[EMPRESA], limit=3000)]
            if b.get("entradas"):
                pids += [m["product_id"][0] for m in odoo.search_read("stock.move", [("picking_id", "in", [int(x) for x in b["entradas"]]), ("state", "=", "done")],
                                                                      ["product_id"], company_ids=[1, 2, 3, 4, 5], limit=5000) if m.get("product_id")]
            if b.get("desde"):
                pids += [a["id"] for a in t_cambios_precio(b["desde"], b.get("hasta"))["articulos"]]
            r = por_tienda(pids)
            t = tienda_de(_u)
            if t:   # una tienda solo ve lo suyo
                r["tiendas"] = {t: r["tiendas"].get(t, [])}
                r["todos"] = []
                r["sin_stock"] = []
            return r
        return guard(run)

    @app.get("/api/etiquetas/entradas")
    def ultimas_entradas(request: Request, dias: int = 15):
        """Últimas recepciones de proveedor validadas (sin los traspasos entre nuestras empresas)."""
        _, cids = ctx(request)

        def run():
            desde = (dt.datetime.now() - dt.timedelta(days=max(1, min(dias, 120)))).strftime("%Y-%m-%d 00:00:00")
            pk = odoo.search_read("stock.picking", [("picking_type_code", "=", "incoming"), ("state", "=", "done"), ("date_done", ">=", desde)],
                                  ["name", "partner_id", "date_done", "origin", "company_id", "move_ids"], company_ids=[1, 2, 3, 4, 5], limit=200, order="date_done desc")
            propias = {c["partner_id"][0] for c in odoo.search_read("res.company", [], ["partner_id"], company_ids=[1, 2, 3, 4, 5])}
            out = []
            for p in pk:
                prov = p.get("partner_id") or [0, "—"]
                out.append({"id": p["id"], "nombre": p["name"], "proveedor": prov[1], "fecha": p["date_done"], "origen": p.get("origin") or "",
                            "lineas": len(p.get("move_ids") or []), "entre_empresas": prov[0] in propias})
            return out
        return guard(run)

    # ---------- pedirlas en lenguaje normal ----------
    def clave():
        f = data_dir / "claude_clave.txt"
        k = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", f.read_text(errors="ignore")) if f.exists() else []
        if not k:
            raise HTTPException(400, "Falta la clave de Claude en el NAS")
        return k[0]

    def t_buscar(texto, limite=80):
        palabras = [w for w in re.split(r"\s+", texto.strip()) if w]
        dom = [("sale_ok", "=", True)]
        for w in palabras[:5]:
            dom += ["|", "|", ("name", "ilike", w), ("default_code", "ilike", w), ("barcode", "=", w)]
        ps = odoo.search_read("product.product", dom, ["display_name", "default_code"], company_ids=[EMPRESA], limit=min(int(limite or 80), 300))
        return {"articulos": [{"id": p["id"], "nombre": p["display_name"], "ref": p.get("default_code") or ""} for p in ps]}

    def t_proveedores(nombre):
        ps = odoo.search_read("res.partner", [("name", "ilike", nombre), ("is_company", "=", True)], ["display_name", "supplier_rank"], company_ids=[1, 2, 3, 4, 5], limit=10)
        ps.sort(key=lambda p: -(p.get("supplier_rank") or 0))
        return {"proveedores": [{"id": p["id"], "nombre": p["display_name"]} for p in ps]}

    def t_entradas(proveedor_id=None, desde=None, hasta=None, documento=None, cids=None):
        dom = [("state", "=", "done"), ("picking_code", "=", "incoming")]
        if proveedor_id:
            dom.append(("picking_id.partner_id", "child_of", int(proveedor_id)))
        if desde:
            dom.append(("date", ">=", desde + " 00:00:00"))
        if hasta:
            dom.append(("date", "<=", hasta + " 23:59:59"))
        if documento:
            d = documento.strip()
            dom += ["|", ("picking_id.name", "ilike", d), ("purchase_line_id.order_id.name", "=ilike", d)]
        if len(dom) <= 2:
            return {"error": "Di al menos un proveedor, unas fechas o un documento"}
        ms = odoo.search_read("stock.move", dom, ["product_id", "quantity", "date", "picking_id"], company_ids=cids, limit=3000, order="date desc")
        acc = {}
        for m in ms:
            if not m.get("product_id"):
                continue
            a = acc.setdefault(m["product_id"][0], {"id": m["product_id"][0], "nombre": m["product_id"][1], "recibido": 0, "entradas": set()})
            a["recibido"] += m.get("quantity") or 0
            if m.get("picking_id"):
                a["entradas"].add(m["picking_id"][1])
        res = [dict(a, entradas=sorted(a["entradas"])) for a in acc.values()]
        return {"articulos": res[:400], "total": len(res)}

    def t_modificados(desde):
        """Artículos con la tarifa o el precio tocados desde una fecha (posibles cambios de precio)."""
        f = desde + " 00:00:00"
        tpl = odoo.search_read("product.template", [("write_date", ">=", f), ("sale_ok", "=", True)], ["id"], company_ids=[EMPRESA], limit=3000)
        it = odoo.search_read("product.pricelist.item", [("pricelist_id", "=", tarifa()), ("write_date", ">=", f)], ["product_id", "product_tmpl_id"], company_ids=[EMPRESA], limit=5000)
        tids = {t["id"] for t in tpl} | {i["product_tmpl_id"][0] for i in it if i.get("product_tmpl_id")}
        pids = {i["product_id"][0] for i in it if i.get("product_id")}
        vs = odoo.search_read("product.product", [("product_tmpl_id", "in", list(tids))], ["display_name", "default_code"], company_ids=[EMPRESA], limit=3000) if tids else []
        ar = {v["id"]: {"id": v["id"], "nombre": v["display_name"]} for v in vs}
        for p in pids - set(ar):
            ar[p] = {"id": p}
        return {"articulos": list(ar.values())[:500], "total": len(ar),
                "aviso": "Odoo no guarda el precio anterior: son los artículos modificados (precio, tarifa u otro dato) desde esa fecha"}

    def t_cargas_precios(dias=200):
        """Días en que se cambiaron muchos precios de golpe (cargas de catálogo)."""
        desde = (dt.date.today() - dt.timedelta(days=int(dias or 200))).isoformat()
        g = odoo.call("mail.tracking.value", "read_group", [1], domain=[("field_id.name", "=", "list_price"), ("field_id.model", "=", "product.template"),
                                                                        ("create_date", ">=", desde)], fields=["id:count"], groupby=["create_date:day"], lazy=False)
        return {"dias": [{"dia": x.get("create_date:day"), "precios_cambiados": x.get("__count")} for x in g if (x.get("__count") or 0) > 0],
                "nota": "Los catálogos trimestrales se cargan de golpe: son los días con cientos de cambios."}

    def t_cambios_precio(desde, hasta=None):
        """Artículos cuyo precio de venta CAMBIÓ de verdad entre dos fechas (historial de Odoo), con precio anterior y nuevo."""
        dom = [("field_id.name", "=", "list_price"), ("field_id.model", "=", "product.template"), ("create_date", ">=", desde + " 00:00:00")]
        if hasta:
            dom.append(("create_date", "<=", hasta + " 23:59:59"))
        tv = odoo.search_read("mail.tracking.value", dom, ["mail_message_id", "old_value_float", "new_value_float"], company_ids=[EMPRESA], limit=20000)
        mids = list({x["mail_message_id"][0] for x in tv if x.get("mail_message_id")})
        msg = {m["id"]: m["res_id"] for m in odoo.search_read("mail.message", [("id", "in", mids)], ["res_id"], company_ids=[EMPRESA], limit=20000)} if mids else {}
        cambio = {}
        for x in tv:
            tid = msg.get((x.get("mail_message_id") or [0])[0])
            if tid and abs((x.get("new_value_float") or 0) - (x.get("old_value_float") or 0)) > 0.0001:
                c = cambio.setdefault(tid, {"antes": x.get("old_value_float"), "ahora": x.get("new_value_float")})
                c["ahora"] = x.get("new_value_float")
        tids = list(cambio)
        vs = odoo.search_read("product.product", [("product_tmpl_id", "in", tids), ("sale_ok", "=", True)], ["display_name", "product_tmpl_id"], company_ids=[EMPRESA], limit=5000) if tids else []
        return {"articulos": [{"id": v["id"], "nombre": v["display_name"], **cambio[v["product_tmpl_id"][0]]} for v in vs][:3000], "total": len(vs)}

    HERR = [
        {"name": "buscar_articulos", "description": "Busca artículos de venta por palabras del nombre, referencia o EAN (todas las palabras deben aparecer).",
         "input_schema": {"type": "object", "properties": {"texto": {"type": "string"}, "limite": {"type": "integer"}}, "required": ["texto"]}},
        {"name": "buscar_proveedor", "description": "Busca proveedores por nombre y devuelve su id.",
         "input_schema": {"type": "object", "properties": {"nombre": {"type": "string"}}, "required": ["nombre"]}},
        {"name": "entradas_material", "description": "Artículos que han ENTRADO (recepciones de compra validadas): por proveedor_id, rango de fechas (AAAA-MM-DD) y/o documento (recepción AC/IN/00137 o pedido de compra P00143). Devuelve cantidad recibida.",
         "input_schema": {"type": "object", "properties": {"proveedor_id": {"type": "integer"}, "desde": {"type": "string"}, "hasta": {"type": "string"}, "documento": {"type": "string"}}}},
        {"name": "cargas_de_precios", "description": "Días en que se cambiaron precios de venta de golpe (cargas de catálogo trimestral). Úsalo para saber cuándo se cargó un catálogo (el más reciente con cientos de cambios es el último catálogo).",
         "input_schema": {"type": "object", "properties": {"dias": {"type": "integer", "description": "cuántos días hacia atrás mirar (por defecto 200)"}}}},
        {"name": "cambios_de_precio", "description": "Artículos cuyo precio de venta CAMBIÓ de verdad entre dos fechas (AAAA-MM-DD), según el historial de Odoo. Para 'nuevos precios del catálogo', 'cambios de precio desde…'. El stock por tienda lo filtra el panel después.",
         "input_schema": {"type": "object", "properties": {"desde": {"type": "string"}, "hasta": {"type": "string"}}, "required": ["desde"]}},
        {"name": "articulos_modificados", "description": "Artículos cuyo precio o tarifa se ha tocado desde una fecha (AAAA-MM-DD). Para 'cambios de precio' o 'catálogo nuevo'.",
         "input_schema": {"type": "object", "properties": {"desde": {"type": "string"}}, "required": ["desde"]}},
        {"name": "resultado", "description": "Devuelve la lista final de etiquetas. cant = nº de etiquetas de cada artículo (1 por defecto, o la cantidad recibida si lo piden).",
         "input_schema": {"type": "object", "properties": {"explicacion": {"type": "string", "description": "una frase: qué has sacado y de dónde"},
                                                            "items": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}, "cant": {"type": "integer"}}, "required": ["id"]}}},
                          "required": ["explicacion", "items"]}},
    ]

    @app.post("/api/etiquetas/pedir")
    async def pedir(request: Request):
        """{texto} -> {explicacion, etiquetas:[...]}"""
        _, cids = ctx(request)
        b = await request.json()
        texto = (b.get("texto") or "").strip()
        if not texto:
            raise HTTPException(400, "Escribe qué etiquetas quieres")

        def run():
            hoy = dt.date.today()
            sistema = (f"Eres el ayudante de etiquetas de precio de Folder Castellón (papelería). Hoy es {hoy.isoformat()} ({['lunes','martes','miércoles','jueves','viernes','sábado','domingo'][hoy.weekday()]}). "
                       "El usuario pide etiquetas de estantería en lenguaje normal. Usa las herramientas para encontrar los artículos (ids de product.product) "
                       "y termina SIEMPRE llamando a 'resultado'. Por defecto 1 etiqueta por artículo; si dice 'una por unidad' o 'tantas como han entrado', usa la cantidad recibida. "
                       "Si dice 'esta semana' es desde el lunes; 'este mes' desde el día 1. Para un proveedor, búscalo primero para tener su id. "
                       "Para 'nuevos precios del catálogo X' o 'cambios de catálogo': mira cargas_de_precios, coge el día de la carga (el último con cientos de cambios si no dice cuál) y usa cambios_de_precio de ese día (desde=hasta=ese día, o un par de días si la carga duró más). "
                       "Lo de 'que tenga stock' lo filtra el panel por tienda: tú devuelve todos los artículos. No inventes ids.")
            msgs = [{"role": "user", "content": texto}]
            recibido = {}
            http = httpx.Client(timeout=120)
            for _ in range(10):
                r = http.post(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages", headers={
                    "x-api-key": clave(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": os.environ.get("PANEL_MODEL", "claude-sonnet-5-5"), "max_tokens": 4000, "system": sistema, "tools": HERR, "messages": msgs})
                if r.status_code != 200:
                    raise HTTPException(502, f"Claude respondió {r.status_code}: {r.text[:200]}")
                bl = r.json().get("content", [])
                msgs.append({"role": "assistant", "content": bl})
                usos = [x for x in bl if x.get("type") == "tool_use"]
                if not usos:
                    txt = "".join(x.get("text", "") for x in bl if x.get("type") == "text")
                    return {"explicacion": txt or "No he encontrado nada", "etiquetas": []}
                res = []
                for u in usos:
                    a = u.get("input") or {}
                    if u["name"] == "resultado":
                        cant = {}
                        for x in a.get("items") or []:
                            cant[int(x["id"])] = max(1, int(round(x.get("cant") or 1)))
                        et = datos_etiquetas(list(cant))
                        for e in et:
                            e["cant"] = cant.get(e["id"], 1)
                            if e["id"] in recibido:
                                e["recibido"] = recibido[e["id"]]
                        return {"explicacion": a.get("explicacion") or "", "etiquetas": et}
                    try:
                        if u["name"] == "buscar_articulos":
                            o = t_buscar(a.get("texto", ""), a.get("limite") or 80)
                        elif u["name"] == "buscar_proveedor":
                            o = t_proveedores(a.get("nombre", ""))
                        elif u["name"] == "entradas_material":
                            o = t_entradas(a.get("proveedor_id"), a.get("desde"), a.get("hasta"), a.get("documento"), cids)
                            for x in o.get("articulos") or []:
                                recibido[x["id"]] = x["recibido"]
                        elif u["name"] == "cargas_de_precios":
                            o = t_cargas_precios(a.get("dias") or 200)
                        elif u["name"] == "cambios_de_precio":
                            o = t_cambios_precio(a.get("desde") or hoy.isoformat(), a.get("hasta"))
                        elif u["name"] == "articulos_modificados":
                            o = t_modificados(a.get("desde") or hoy.isoformat())
                        else:
                            o = {"error": "herramienta desconocida"}
                    except Exception as e:
                        o = {"error": str(e)[:300]}
                    res.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(o, ensure_ascii=False, default=str)[:60000]})
                msgs.append({"role": "user", "content": res})
            raise HTTPException(500, "No he terminado de buscar: prueba a concretar más")
        return guard(run)
