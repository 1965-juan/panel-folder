"""Inventarios: recuentos con la PDA (lector en modo teclado + Enter) o con el ordenador, en almacén y tiendas.
El recuento se guarda en el panel; el administrador ve las diferencias con Odoo y lo aplica (ajuste de inventario en Odoo).
Recuentos parciales: solo se ajustan los artículos contados; lo no contado se queda como está.
"""
import csv
import datetime as dt
import io
import json
import sqlite3
import threading
import time

from fastapi import Request, HTTPException
from fastapi.responses import Response

TODAS = [1, 2, 3, 4, 5]
# código -> ubicación de stock en Odoo
UBIC = {
    "AC": {"nombre": "Almacén", "empresa": 1, "loc": 8},
    "T1": {"nombre": "Tienda 1", "empresa": 1, "loc": 34},
    "T2": {"nombre": "Tienda 2", "empresa": 1, "loc": 40},
    "VP": {"nombre": "Logipaper (Tienda Logipaper / VP)", "empresa": 4, "loc": 46},
}
# usuario del panel -> ubicaciones que puede contar (el resto de usuarios con el módulo: todas)
POR_USUARIO = {"almacen": ["AC"], "tienda1": ["T1"], "tienda2": ["T2"], "tiendalogipaper": ["VP"]}


def setup(app, *, odoo, current_user, guard, es_admin, data_dir, nombre_usuario):
    lock = threading.Lock()
    db = data_dir / "inventario.db"

    def con():
        c = sqlite3.connect(db, timeout=20)
        c.row_factory = sqlite3.Row
        c.executescript("""
        CREATE TABLE IF NOT EXISTS recuentos (id INTEGER PRIMARY KEY AUTOINCREMENT, ubic TEXT, nombre TEXT, estado TEXT, usuario TEXT, cuando REAL,
            cerrado_por TEXT, cerrado REAL, aplicado_por TEXT, aplicado REAL, resultado TEXT);
        CREATE TABLE IF NOT EXISTS lineas (id INTEGER PRIMARY KEY AUTOINCREMENT, rid INTEGER, product_id INTEGER, codigo TEXT, nombre TEXT, ref TEXT,
            cant REAL, usuario TEXT, cuando REAL);
        CREATE INDEX IF NOT EXISTS ix_lin ON lineas (rid, product_id);
        """)
        return c

    # ---------- caché de códigos de barras (para que cada lectura sea instantánea) ----------
    cache = {"t": 0, "cod": {}, "ref": {}, "cargando": False}

    def cargar_cache(forzar=False):
        if not forzar and cache["cod"] and time.time() - cache["t"] < 900:
            return
        with lock:
            if not forzar and cache["cod"] and time.time() - cache["t"] < 900:
                return
            cod, ref = {}, {}
            ps = odoo.search_read("product.product", [("active", "=", True), "|", ("barcode", "!=", False), ("default_code", "!=", False)],
                                  ["barcode", "default_code", "display_name"], TODAS, limit=200000)
            for p in ps:
                d = {"id": p["id"], "nombre": p["display_name"], "ref": p.get("default_code") or "", "ud": 1}
                if p.get("barcode"):
                    cod[p["barcode"].strip()] = d
                if p.get("default_code"):
                    ref[p["default_code"].strip().upper()] = d
            try:   # cajas / paquetes con su propio código (suman las unidades de la caja)
                for k in odoo.search_read("product.packaging", [("barcode", "!=", False)], ["barcode", "product_id", "qty"], TODAS, limit=50000):
                    if k.get("product_id") and k["barcode"].strip() not in cod:
                        base = next((x for x in cod.values() if x["id"] == k["product_id"][0]), None) or {"id": k["product_id"][0], "nombre": k["product_id"][1], "ref": ""}
                        cod[k["barcode"].strip()] = {**base, "ud": k.get("qty") or 1, "caja": True}
            except Exception:
                pass
            cache.update(t=time.time(), cod=cod, ref=ref)

    etq = {"t": 0, "m": {}}

    def etiquetas(k):
        """Códigos de barras de la etiqueta de cada ubicación (los de Odoo, aunque los renombréis: AC-STOCK, T1-STOCK…)."""
        if time.time() - etq["t"] > 900:
            try:
                ls = odoo.search_read("stock.location", [("id", "in", [v["loc"] for v in UBIC.values()])], ["barcode"], TODAS)
                etq["m"] = {x["id"]: (x.get("barcode") or "").upper() for x in ls}
                etq["t"] = time.time()
            except Exception:
                pass
        base = {"AC": {"WHSTOCK", "AC-STOCK", "ACSTOCK"}, "T1": {"T1STOCK", "T1-STOCK"}, "T2": {"T2STOCK", "T2-STOCK"}, "VP": {"LOGIPSTOCK", "VP-STOCK", "VPSTOCK"}}[k]
        b = etq["m"].get(UBIC[k]["loc"])
        return base | ({b} if b else set())

    def buscar_codigo(c):
        c = (c or "").strip()
        cargar_cache()
        if c in cache["cod"]:
            return cache["cod"][c]
        if c.upper() in cache["ref"]:
            return cache["ref"][c.upper()]
        if c.isdigit() and len(c) in (12, 13):   # UPC-A leído como EAN-13 o al revés
            for v in (c.lstrip("0"), "0" + c):
                if v in cache["cod"]:
                    return cache["cod"][v]
        return None

    # ---------- permisos ----------
    def puede(u):
        if es_admin(u):
            return list(UBIC)
        return POR_USUARIO.get(u, list(UBIC))

    def recuento(rid, u):
        with con() as c:
            r = c.execute("SELECT * FROM recuentos WHERE id=?", (rid,)).fetchone()
        if not r or r["ubic"] not in puede(u):
            raise HTTPException(404, "No encuentro ese recuento")
        return dict(r)

    def abierto(r):
        if r["estado"] != "abierto":
            raise HTTPException(400, "Este recuento ya está cerrado")

    def lineas(rid):
        with con() as c:
            return [dict(x) for x in c.execute("SELECT * FROM lineas WHERE rid=? ORDER BY cuando DESC", (rid,)).fetchall()]

    # ---------- API ----------
    @app.get("/api/inv/inicio")
    def inicio(request: Request):
        u = current_user(request)
        us = puede(u)
        threading.Thread(target=lambda: guard(cargar_cache), daemon=True).start()   # precarga para que la primera lectura vaya rápida
        with con() as c:
            rs = [dict(x) for x in c.execute(f"""SELECT r.*, COUNT(l.id) refs, COALESCE(SUM(l.cant),0) uds FROM recuentos r LEFT JOIN lineas l ON l.rid=r.id
                  WHERE r.ubic IN ({','.join('?' * len(us))}) AND (r.estado!='aplicado' OR r.aplicado > ?) GROUP BY r.id ORDER BY r.id DESC""",
                                              (*us, time.time() - 30 * 86400)).fetchall()]
        for r in rs:
            r.pop("resultado", None)
            r["ubic_nombre"] = UBIC[r["ubic"]]["nombre"]
        return {"ubicaciones": [{"codigo": k, "nombre": UBIC[k]["nombre"]} for k in us], "recuentos": rs, "admin": es_admin(u),
                "usuario": nombre_usuario(u)}

    @app.post("/api/inv/nuevo")
    async def nuevo(request: Request):
        u = current_user(request)
        b = await request.json()
        ub = (b.get("ubic") or "").upper()
        if ub not in puede(u):
            raise HTTPException(400, "No puedes contar en esa ubicación")
        nom = (b.get("nombre") or "").strip()[:80] or f"Recuento {dt.date.today().strftime('%d/%m/%Y')}"
        with lock, con() as c:
            rid = c.execute("INSERT INTO recuentos (ubic, nombre, estado, usuario, cuando) VALUES (?,?,?,?,?)", (ub, nom, "abierto", u, time.time())).lastrowid
        return {"id": rid}

    @app.get("/api/inv/r/{rid}")
    def ver(rid: int, request: Request):
        u = current_user(request)
        r = recuento(rid, u)
        r.pop("resultado", None)
        r["ubic_nombre"] = UBIC[r["ubic"]]["nombre"]
        return {"recuento": r, "lineas": lineas(rid)}

    @app.post("/api/inv/r/{rid}/leer")
    async def leer(rid: int, request: Request):
        """{codigo, cant}: suma `cant` (por defecto 1) al artículo de ese código. Si el código es de una caja, suma las unidades de la caja."""
        u = current_user(request)
        r = recuento(rid, u)
        abierto(r)
        b = await request.json()
        cod = (b.get("codigo") or "").strip()
        if not cod:
            raise HTTPException(400, "Código vacío")
        try:
            cant = float(b.get("cant") if b.get("cant") not in (None, "") else 1)
        except ValueError:
            raise HTTPException(400, "Cantidad no válida")
        # ¿es la etiqueta de una ubicación?
        for k, v in UBIC.items():
            if cod.upper() in etiquetas(k):
                ok = k == r["ubic"]
                return {"ubicacion": True, "ok": ok, "nombre": v["nombre"],
                        "msg": f"Ubicación correcta: {v['nombre']}" if ok else f"¡Ojo! Esa etiqueta es de {v['nombre']} y este recuento es de {UBIC[r['ubic']]['nombre']}"}
        p = guard(lambda: buscar_codigo(cod))
        uds = cant * (p["ud"] if p else 1)
        with lock, con() as c:
            if p:
                x = c.execute("SELECT id, cant FROM lineas WHERE rid=? AND product_id=?", (rid, p["id"])).fetchone()
            else:
                x = c.execute("SELECT id, cant FROM lineas WHERE rid=? AND product_id IS NULL AND codigo=?", (rid, cod)).fetchone()
            if x:
                total = x["cant"] + uds
                c.execute("UPDATE lineas SET cant=?, usuario=?, cuando=? WHERE id=?", (total, u, time.time(), x["id"]))
                lid = x["id"]
            else:
                total = uds
                lid = c.execute("INSERT INTO lineas (rid, product_id, codigo, nombre, ref, cant, usuario, cuando) VALUES (?,?,?,?,?,?,?,?)",
                                (rid, p["id"] if p else None, cod, p["nombre"] if p else "", p["ref"] if p else "", uds, u, time.time())).lastrowid
        return {"ok": bool(p), "id": lid, "codigo": cod, "nombre": p["nombre"] if p else "", "ref": p["ref"] if p else "", "sumado": uds,
                "total": total, "caja": bool(p and p.get("caja"))}

    @app.post("/api/inv/r/{rid}/linea")
    async def linea(rid: int, request: Request):
        """{id, cant} cambia la cantidad (0 = quitar) · {id, product_id} asigna un artículo a un código no encontrado."""
        u = current_user(request)
        r = recuento(rid, u)
        abierto(r)
        b = await request.json()
        lid = int(b.get("id") or 0)
        with lock, con() as c:
            x = c.execute("SELECT * FROM lineas WHERE id=? AND rid=?", (lid, rid)).fetchone()
            if not x:
                raise HTTPException(404, "No encuentro esa línea")
            if b.get("product_id"):
                pid = int(b["product_id"])
                pr = guard(lambda: odoo.search_read("product.product", [("id", "=", pid)], ["display_name", "default_code"], TODAS))
                if not pr:
                    raise HTTPException(404, "No encuentro el artículo")
                y = c.execute("SELECT id, cant FROM lineas WHERE rid=? AND product_id=? AND id!=?", (rid, pid, lid)).fetchone()
                if y:   # ya estaba contado: se juntan
                    c.execute("UPDATE lineas SET cant=? WHERE id=?", (y["cant"] + x["cant"], y["id"]))
                    c.execute("DELETE FROM lineas WHERE id=?", (lid,))
                else:
                    c.execute("UPDATE lineas SET product_id=?, nombre=?, ref=? WHERE id=?", (pid, pr[0]["display_name"], pr[0].get("default_code") or "", lid))
            else:
                q = float(b.get("cant") or 0)
                if q <= 0:
                    c.execute("DELETE FROM lineas WHERE id=?", (lid,))
                else:
                    c.execute("UPDATE lineas SET cant=?, usuario=?, cuando=? WHERE id=?", (q, u, time.time(), lid))
        return {"ok": True}

    @app.get("/api/inv/buscar")
    def buscar(request: Request, q: str = ""):
        current_user(request)
        q = q.strip()
        if len(q) < 2:
            return []

        def run():
            dom = [("active", "=", True)]
            for w in q.split():
                dom += ["|", "|", ("name", "ilike", w), ("default_code", "ilike", w), ("barcode", "ilike", w)]
            ps = odoo.search_read("product.product", dom, ["display_name", "default_code", "barcode"], TODAS, limit=30, order="default_code")
            return [{"id": p["id"], "nombre": p["display_name"], "ref": p.get("default_code") or "", "ean": p.get("barcode") or ""} for p in ps]
        return guard(run)

    @app.post("/api/inv/r/{rid}/manual")
    async def manual(rid: int, request: Request):
        """{product_id, cant}: añade un artículo buscado por nombre (los que no tienen código de barras)."""
        u = current_user(request)
        r = recuento(rid, u)
        abierto(r)
        b = await request.json()
        pid = int(b.get("product_id") or 0)
        cant = float(b.get("cant") or 0)
        if cant <= 0:
            raise HTTPException(400, "Pon una cantidad")
        pr = guard(lambda: odoo.search_read("product.product", [("id", "=", pid)], ["display_name", "default_code", "barcode"], TODAS))
        if not pr:
            raise HTTPException(404, "No encuentro el artículo")
        with lock, con() as c:
            x = c.execute("SELECT id, cant FROM lineas WHERE rid=? AND product_id=?", (rid, pid)).fetchone()
            if x:
                c.execute("UPDATE lineas SET cant=?, usuario=?, cuando=? WHERE id=?", (x["cant"] + cant, u, time.time(), x["id"]))
            else:
                c.execute("INSERT INTO lineas (rid, product_id, codigo, nombre, ref, cant, usuario, cuando) VALUES (?,?,?,?,?,?,?,?)",
                          (rid, pid, pr[0].get("barcode") or "", pr[0]["display_name"], pr[0].get("default_code") or "", cant, u, time.time()))
        return {"ok": True}

    @app.post("/api/inv/r/{rid}/estado")
    async def estado(rid: int, request: Request):
        """{accion: cerrar|reabrir|borrar}. Cerrar = terminado de contar, pasa a revisión del administrador."""
        u = current_user(request)
        r = recuento(rid, u)
        a = (await request.json()).get("accion")
        with lock, con() as c:
            if a == "cerrar":
                abierto(r)
                c.execute("UPDATE recuentos SET estado='cerrado', cerrado_por=?, cerrado=? WHERE id=?", (u, time.time(), rid))
            elif a == "reabrir":
                if r["estado"] == "aplicado":
                    raise HTTPException(400, "Ya está aplicado en Odoo")
                c.execute("UPDATE recuentos SET estado='abierto' WHERE id=?", (rid,))
            elif a == "borrar":
                if r["estado"] == "aplicado":
                    raise HTTPException(400, "Ya está aplicado en Odoo")
                if not es_admin(u) and r["usuario"] != u:
                    raise HTTPException(403, "Solo lo puede borrar quien lo empezó o el administrador")
                c.execute("DELETE FROM lineas WHERE rid=?", (rid,))
                c.execute("DELETE FROM recuentos WHERE id=?", (rid,))
            else:
                raise HTTPException(400, "Acción no válida")
        return {"ok": True}

    def teorico(U, pids):
        q = odoo.search_read("stock.quant", [("product_id", "in", pids), ("location_id", "child_of", U["loc"])],
                             ["product_id", "location_id", "quantity", "inventory_quantity_set", "lot_id"], [U["empresa"]] + [x for x in TODAS if x != U["empresa"]],
                             limit=50000) if pids else []
        out = {}
        for x in q:
            out.setdefault(x["product_id"][0], []).append(x)
        return out

    @app.get("/api/inv/r/{rid}/diferencias")
    def diferencias(rid: int, request: Request):
        u = current_user(request)
        r = recuento(rid, u)
        U = UBIC[r["ubic"]]
        ls = lineas(rid)

        def run():
            pids = list({l["product_id"] for l in ls if l["product_id"]})
            st = teorico(U, pids)
            cost = {p["id"]: p.get("standard_price") or 0 for p in odoo.search_read("product.product", [("id", "in", pids)], ["standard_price"], [U["empresa"]])} if pids else {}
            out, sin = [], []
            for l in sorted(ls, key=lambda x: (x["nombre"] or x["codigo"]).lower()):
                if not l["product_id"]:
                    sin.append(l)
                    continue
                t = round(sum(x["quantity"] for x in st.get(l["product_id"], [])), 3)
                d = round(l["cant"] - t, 3)
                out.append({**l, "teorico": t, "dif": d, "coste": cost.get(l["product_id"], 0), "valor": round(d * cost.get(l["product_id"], 0), 2)})
            res = json.loads(r["resultado"]) if r.get("resultado") else None
            return {"recuento": {k: v for k, v in r.items() if k != "resultado"}, "ubic_nombre": U["nombre"], "lineas": out, "sin_articulo": sin,
                    "resumen": {"refs": len(out), "con_dif": sum(1 for x in out if abs(x["dif"]) > 0.0001), "valor": round(sum(x["valor"] for x in out), 2)},
                    "resultado": res, "admin": es_admin(u)}
        return guard(run)

    @app.post("/api/inv/r/{rid}/aplicar")
    async def aplicar(rid: int, request: Request):
        """Solo administrador: ajusta en Odoo el stock de los artículos contados (lo no contado no se toca)."""
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "Solo el administrador aplica los recuentos en Odoo")
        r = recuento(rid, u)
        if r["estado"] == "aplicado":
            raise HTTPException(400, "Este recuento ya está aplicado")
        U = UBIC[r["ubic"]]
        E = U["empresa"]
        ls = [l for l in lineas(rid) if l["product_id"]]
        if not ls:
            raise HTTPException(400, "No hay artículos contados")

        def run():
            st = teorico(U, [l["product_id"] for l in ls])
            ctx = {"inventory_mode": True, "inventory_name": f"Recuento panel #{rid} {r['nombre']}"[:120]}
            qids, cambios, errores = [], [], []
            for l in ls:
                qs = st.get(l["product_id"], [])
                t = sum(x["quantity"] for x in qs)
                if abs(l["cant"] - t) < 0.0001:
                    continue
                try:
                    principal = [x for x in qs if x["location_id"][0] == U["loc"] and not x.get("lot_id")]
                    otros = sum(x["quantity"] for x in qs if not principal or x["id"] != principal[0]["id"])
                    objetivo = l["cant"] - otros
                    if principal:
                        odoo.call("stock.quant", "write", [E], ids=[principal[0]["id"]], vals={"inventory_quantity": objetivo}, context=dict(ctx))
                        qid = principal[0]["id"]
                    else:
                        qid = odoo.call("stock.quant", "create", [E], vals_list=[{"product_id": l["product_id"], "location_id": U["loc"],
                                                                                  "inventory_quantity": objetivo}], context=dict(ctx))
                        qid = qid[0] if isinstance(qid, list) else qid
                    qids.append(qid)
                    cambios.append({"producto": l["nombre"], "antes": t, "ahora": l["cant"]})
                except Exception as e:
                    errores.append({"producto": l["nombre"], "error": str(e)[:200]})
            if qids:
                for i in range(0, len(qids), 100):
                    odoo.call("stock.quant", "action_apply_inventory", [E], ids=qids[i:i + 100], context=dict(ctx))
            res = {"ajustados": len(cambios), "sin_cambio": len(ls) - len(cambios) - len(errores), "errores": errores, "cambios": cambios}
            with lock, con() as c:
                c.execute("UPDATE recuentos SET estado=?, aplicado_por=?, aplicado=?, resultado=? WHERE id=?",
                          ("aplicado" if not errores else "cerrado", u, time.time(), json.dumps(res, ensure_ascii=False), rid))
            return res
        return guard(run)

    @app.get("/api/inv/r/{rid}/excel")
    def excel(rid: int, request: Request):
        u = current_user(request)
        r = recuento(rid, u)
        f = io.StringIO()
        w = csv.writer(f, delimiter=";")
        w.writerow(["Código de barras", "Referencia", "Artículo", "Ubicación", "Cantidad", "Contado por"])
        for l in sorted(lineas(rid), key=lambda x: (x["nombre"] or x["codigo"]).lower()):
            w.writerow([l["codigo"], l["ref"], l["nombre"] or "(no encontrado)", UBIC[r["ubic"]]["nombre"], str(l["cant"]).replace(".", ","), l["usuario"]])
        return Response("﻿" + f.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="recuento_{rid}_{r["ubic"]}.csv"'})
