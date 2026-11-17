"""TPV de emergencia: para seguir vendiendo en las tiendas y el almacén cuando Odoo (o internet) no va.

- El catálogo (artículos del TPV con códigos de barras, precio con IVA e impuesto) se guarda en el NAS y se renueva solo
  cada pocas horas mientras Odoo responde; el navegador de cada tienda se lo guarda también para poder vender sin conexión.
- Las ventas se guardan en el navegador y, en cuanto hay conexión con el panel, en el NAS (datos/emergencia/ventas.json).
- Un hilo las vuelca solas a Odoo como tickets del TPV de esa tienda en cuanto hay una caja abierta (pos.order.sync_from_ui):
  entran en la caja del día con su forma de pago y descuentan stock.
"""
import datetime as dt
import json
import threading
import time
import uuid as _uuid

from fastapi import Request, HTTPException

# tienda -> empresa, TPV (pos.config) y formas de pago (pos.payment.method)
PUNTOS = {
    "T1": {"nombre": "Tienda 1", "empresa": 1, "pos": 2, "efectivo": 4, "tarjeta": 19},
    "T2": {"nombre": "Tienda 2", "empresa": 1, "pos": 4, "efectivo": 6, "tarjeta": 20},
    "T3": {"nombre": "Tienda Logipaper", "empresa": 4, "pos": 5, "efectivo": 12, "tarjeta": 13},
    "AC": {"nombre": "Almacén", "empresa": 1, "pos": 1, "efectivo": 1, "tarjeta": 2},
}
VARIOS = "VARIOS (TPV EMERGENCIA)"
CADA = 4 * 3600          # renovar catálogo
REINTENTO = 300          # volcado a Odoo


def setup(app, *, odoo, current_user, guard, tienda_de, es_admin, data_dir):
    carpeta = data_dir / "emergencia"
    carpeta.mkdir(parents=True, exist_ok=True)
    f_ventas = carpeta / "ventas.json"
    lock = threading.Lock()
    estado = {"ultimo_volcado": 0, "ultimo_error": "", "catalogo": {}}

    # ---------------- ventas en el NAS ----------------
    def leer():
        try:
            return json.loads(f_ventas.read_text())
        except Exception:
            return []

    def guardar(vs):
        tmp = f_ventas.with_suffix(".tmp")
        tmp.write_text(json.dumps(vs, ensure_ascii=False))
        tmp.replace(f_ventas)

    def punto_de(request, t=None):
        u = current_user(request)
        propia = tienda_de(u) or ("AC" if u == "almacen" else None)
        if propia:
            return u, propia
        if not es_admin(u):
            raise HTTPException(403, "Sin acceso al TPV de emergencia")
        t = (t or "T1").upper()
        if t not in PUNTOS:
            raise HTTPException(400, "Tienda no válida")
        return u, t

    # ---------------- catálogo ----------------
    def f_cat(emp):
        return carpeta / f"catalogo_{emp}.json"

    def construir(emp):
        T = [emp]
        prods = odoo.search_read("product.product", [("available_in_pos", "=", True), ("sale_ok", "=", True)],
                                 ["display_name", "default_code", "barcode", "lst_price", "taxes_id", "product_tmpl_id"], T, limit=200000)
        imp = {t["id"]: t for t in odoo.search_read("account.tax", [("type_tax_use", "=", "sale"), ("company_id", "=", emp)],
                                                       ["amount", "amount_type", "price_include", "name"], T, limit=500)}
        emb = {}
        try:
            for p in odoo.search_read("product.packaging", [("barcode", "!=", False)], ["product_id", "barcode", "qty"], T, limit=100000):
                if p.get("product_id"):
                    emb.setdefault(p["product_id"][0], []).append([p["barcode"], p["qty"] or 1])
        except Exception:
            pass
        out = []
        for p in prods:
            tx = [imp[t] for t in p["taxes_id"] if t in imp]
            tasa = sum(t["amount"] for t in tx if t["amount_type"] == "percent") / 100
            incl = any(t.get("price_include") for t in tx)
            base = p["lst_price"] or 0
            pvp = round(base if incl else base * (1 + tasa), 2)
            cods = [[p["barcode"], 1]] if p.get("barcode") else []
            cods += emb.get(p["id"], [])
            out.append([p["id"], p["display_name"], p.get("default_code") or "", pvp, round(tasa, 4), cods])
        d = {"empresa": emp, "cuando": time.time(), "fecha": dt.datetime.now().strftime("%d/%m/%Y %H:%M"), "articulos": out}
        tmp = f_cat(emp).with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
        tmp.replace(f_cat(emp))
        estado["catalogo"][emp] = d["fecha"]
        return d

    def catalogo(emp, forzar=False):
        f = f_cat(emp)
        viejo = (not f.exists()) or (time.time() - f.stat().st_mtime > CADA)
        if forzar or viejo:
            try:
                return construir(emp)
            except Exception:
                if not f.exists():
                    raise
        return json.loads(f.read_text())

    # ---------------- volcado a Odoo ----------------
    def producto_varios(emp):
        x = odoo.search_read("product.product", [("name", "=", VARIOS)], ["id"], [1, 2, 3, 4, 5], limit=1)
        if x:
            return x[0]["id"]
        vals = {"name": VARIOS, "sale_ok": True, "purchase_ok": False, "type": "consu", "list_price": 0, "available_in_pos": True, "is_published": False}
        tm = odoo.call("product.template", "create", [emp], vals_list=[vals])
        tm = tm[0] if isinstance(tm, list) else tm
        return odoo.search_read("product.product", [("product_tmpl_id", "=", tm)], ["id"], [emp])[0]["id"]

    def volcar_una(v):
        P = PUNTOS[v["tienda"]]
        E = [P["empresa"]]
        ses = odoo.search_read("pos.session", [("config_id", "=", P["pos"]), ("state", "=", "opened")], ["id", "name", "user_id"], E, limit=1)
        if not ses:
            return False, f"Esperando a que se abra la caja de {P['nombre']} en Odoo"
        ses = ses[0]
        ya = odoo.search_read("pos.order", [("uuid", "=", v["uid"])], ["id", "name"], E, limit=1)
        if ya:
            return True, ya[0]["name"]
        pids = [l["product_id"] for l in v["lineas"] if l.get("product_id")]
        info = {p["id"]: p for p in odoo.search_read("product.product", [("id", "in", pids)], ["taxes_id", "display_name"], E)} if pids else {}
        imp = {t["id"]: t for t in odoo.search_read("account.tax", [("type_tax_use", "=", "sale"), ("company_id", "=", P["empresa"])],
                                                       ["amount", "amount_type", "price_include"], E, limit=500)}
        lineas, total, tax_total = [], 0.0, 0.0
        for l in v["lineas"]:
            pid = l.get("product_id") or producto_varios(P["empresa"])
            txs = [t for t in (info.get(pid, {}).get("taxes_id") or []) if t in imp]
            if not l.get("product_id"):
                txs = [t for t in imp if imp[t]["amount"] == 21 and imp[t]["amount_type"] == "percent" and not imp[t]["price_include"]][:1]
            tasa = sum(imp[t]["amount"] for t in txs if imp[t]["amount_type"] == "percent") / 100
            q = float(l["cant"])
            incl_u = float(l["precio"])           # precio unitario con IVA, tal cual se cobró
            sub_incl = round(incl_u * q, 2)
            sub = round(sub_incl / (1 + tasa), 2)
            lineas.append([0, 0, {"product_id": pid, "qty": q, "price_unit": round(incl_u / (1 + tasa), 6), "discount": 0,
                                  "tax_ids": [[6, 0, txs]], "price_subtotal": sub, "price_subtotal_incl": sub_incl,
                                  "full_product_name": l.get("nombre") or info.get(pid, {}).get("display_name") or VARIOS}])
            total += sub_incl
            tax_total += sub_incl - sub
        total = round(total, 2)
        pagos, pagado = [], 0.0
        fecha = v["fecha_utc"]
        for p in v["pagos"]:
            mid = P["tarjeta"] if p["forma"] == "tarjeta" else P["efectivo"]
            pagos.append([0, 0, {"payment_method_id": mid, "amount": round(float(p["importe"]), 2), "payment_date": fecha}])
            pagado += float(p["importe"])
        cambio = round(pagado - total, 2)
        if cambio > 0:   # el cambio sale del efectivo, como hace el TPV
            pagos.append([0, 0, {"payment_method_id": P["efectivo"], "amount": -cambio, "payment_date": fecha, "is_change": True}])
        orden = {"uuid": v["uid"], "session_id": ses["id"], "date_order": fecha, "lines": lineas, "payment_ids": pagos,
                 "amount_total": total, "amount_tax": round(tax_total, 2), "amount_paid": total, "amount_return": 0,
                 "state": "paid", "partner_id": False, "to_invoice": False, "pricelist_id": False, "fiscal_position_id": False,
                 "general_customer_note": f"TPV emergencia · vendido el {v['fecha']}", "company_id": P["empresa"]}
        if ses.get("user_id"):
            orden["user_id"] = ses["user_id"][0]
        try:
            odoo.call_kw("pos.order", "sync_from_ui", [[orden]], company_ids=E)
        except Exception as e:
            m = str(e)
            opc = [k for k in ("general_customer_note", "is_change", "to_invoice") if k in m]
            if not opc:
                raise
            for k in opc:   # campos que esta versión de Odoo no conoce
                orden.pop(k, None)
                for p in orden["payment_ids"]:
                    p[2].pop(k, None)
            odoo.call_kw("pos.order", "sync_from_ui", [[orden]], company_ids=E)
        ya = odoo.search_read("pos.order", [("uuid", "=", v["uid"])], ["name", "state", "amount_total"], E, limit=1)
        if not ya:
            raise Exception("Odoo no ha creado el ticket")
        return True, ya[0]["name"]

    def volcar():
        with lock:
            vs = leer()
            hechos = 0
            for v in vs:
                if v.get("estado") == "volcada":
                    continue
                try:
                    ok, txt = volcar_una(v)
                    if ok:
                        v.update(estado="volcada", ticket=txt, volcada=time.time(), error="")
                        hechos += 1
                    else:
                        v.update(estado="pendiente", error=txt)
                except Exception as e:
                    v.update(estado="error", error=str(e)[:300], intentos=v.get("intentos", 0) + 1)
                    estado["ultimo_error"] = str(e)[:300]
                guardar(vs)
            estado["ultimo_volcado"] = time.time()
            return hechos

    def bucle():
        time.sleep(60)
        while True:
            try:
                if any(v.get("estado") != "volcada" for v in leer()):
                    volcar()
            except Exception as e:
                estado["ultimo_error"] = str(e)[:300]
            try:   # catálogo al día en el NAS
                for emp in (1, 4):
                    f = f_cat(emp)
                    if (not f.exists()) or time.time() - f.stat().st_mtime > CADA:
                        construir(emp)
            except Exception:
                pass
            time.sleep(REINTENTO)
    threading.Thread(target=bucle, daemon=True).start()

    # ---------------- API ----------------
    @app.get("/api/emerg/inicio")
    def inicio(request: Request, t: str = ""):
        u, t = punto_de(request, t)
        P = PUNTOS[t]
        f = f_cat(P["empresa"])
        vs = [v for v in leer() if v["tienda"] == t]
        return {"tienda": t, "nombre": P["nombre"], "empresa": P["empresa"], "admin": es_admin(u),
                "tiendas": {k: x["nombre"] for k, x in PUNTOS.items()},
                "catalogo": json.loads(f.read_text())["fecha"] if f.exists() else None,
                "pendientes": sum(1 for v in vs if v.get("estado") != "volcada"),
                "ultimas": [{k: v.get(k) for k in ("uid", "fecha", "total", "estado", "ticket", "error")} for v in vs[-30:]][::-1]}

    @app.get("/api/emerg/catalogo")
    def api_catalogo(request: Request, t: str = "", forzar: int = 0):
        u, t = punto_de(request, t)
        return guard(lambda: catalogo(PUNTOS[t]["empresa"], bool(forzar)))

    @app.post("/api/emerg/ventas")
    async def api_ventas(request: Request):
        u, _ = punto_de(request)
        b = await request.json()
        nuevas = 0
        with lock:
            vs = leer()
            uids = {v["uid"] for v in vs}
            for v in b.get("ventas") or []:
                t = (v.get("tienda") or "").upper()
                if t not in PUNTOS or not v.get("lineas"):
                    continue
                if not es_admin(u):
                    _, propia = punto_de(request)
                    if propia != t:
                        continue
                uid = str(v.get("uid") or _uuid.uuid4())
                if uid in uids:
                    continue
                try:
                    fl = dt.datetime.fromisoformat(v["fecha_iso"].replace("Z", "+00:00"))
                    futc = fl.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    futc = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                vs.append({"uid": uid, "tienda": t, "usuario": u, "fecha": v.get("fecha") or "", "fecha_utc": futc,
                           "lineas": [{"product_id": l.get("product_id"), "nombre": (l.get("nombre") or "")[:120],
                                       "cant": float(l.get("cant") or 1), "precio": float(l.get("precio") or 0)} for l in v["lineas"]],
                           "pagos": [{"forma": "tarjeta" if p.get("forma") == "tarjeta" else "efectivo", "importe": float(p.get("importe") or 0)}
                                     for p in v.get("pagos") or []],
                           "total": round(float(v.get("total") or 0), 2), "estado": "pendiente", "recibida": time.time()})
                uids.add(uid)
                nuevas += 1
            guardar(vs)
        if nuevas:
            threading.Thread(target=lambda: _seguro(volcar), daemon=True).start()
        return {"ok": True, "recibidas": nuevas}

    def _seguro(fn):
        try:
            fn()
        except Exception as e:
            estado["ultimo_error"] = str(e)[:300]

    @app.post("/api/emerg/volcar")
    def api_volcar(request: Request):
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403)
        n = volcar()
        vs = leer()
        return {"volcadas_ahora": n, "pendientes": sum(1 for v in vs if v.get("estado") != "volcada"),
                "errores": [{"uid": v["uid"], "tienda": v["tienda"], "error": v.get("error")} for v in vs if v.get("estado") == "error"][:20]}

    @app.get("/api/emerg/admin")
    def api_admin(request: Request):
        if not es_admin(current_user(request)):
            raise HTTPException(403)
        vs = leer()
        return {"estado": {**estado}, "total": len(vs),
                "por_estado": {e: sum(1 for v in vs if v.get("estado") == e) for e in ("pendiente", "error", "volcada")},
                "ultimas": [{k: v.get(k) for k in ("uid", "tienda", "fecha", "total", "estado", "ticket", "error")} for v in vs[-40:]][::-1]}
