"""Inventarios: recuentos con la PDA (lector en modo teclado + Enter) o con el ordenador, en almacén y tiendas.
El recuento se guarda en el panel; el administrador ve las diferencias con Odoo y lo aplica (ajuste de inventario en Odoo).
Recuentos parciales: solo se ajustan los artículos contados; lo no contado se queda como está.
"""
import csv
import re
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
    # almacén provisional (Distriplus) para material que entra sin saber aún a qué tienda va: no es de ninguna tienda ni del almacén central
    "LOTE": {"nombre": "Lote provisional (sin repartir)", "empresa": 1, "loc": None},
}
# usuario del panel -> ubicaciones que puede contar (el resto de usuarios con el módulo: todas)
POR_USUARIO = {"almacen": ["AC", "LOTE"], "tienda1": ["T1"], "tienda2": ["T2"], "tiendalogipaper": ["VP"]}
LOTE_BARCODE = "LOTE-PROVISIONAL"


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
        CREATE TABLE IF NOT EXISTS inventarios (id INTEGER PRIMARY KEY AUTOINCREMENT, ubic TEXT, nombre TEXT, estado TEXT, usuario TEXT, cuando REAL,
            aplicado_por TEXT, aplicado REAL, resultado TEXT);
        """)
        try:
            c.execute("ALTER TABLE recuentos ADD COLUMN inv_id INTEGER")
        except sqlite3.OperationalError:
            pass
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

    def lote_loc():
        """La ubicación del lote provisional en Odoo (AC/Lote provisional, al lado de AC/Stock, fuera de él). Se crea la primera vez."""
        if UBIC["LOTE"]["loc"]:
            return UBIC["LOTE"]["loc"]
        x = odoo.search_read("stock.location", [("barcode", "=", LOTE_BARCODE)], ["id"], [1], limit=1)
        if not x:
            padre = odoo.search_read("stock.location", [("id", "=", UBIC["AC"]["loc"])], ["location_id"], [1], limit=1)[0]["location_id"][0]
            nid = odoo.call("stock.location", "create", [1], vals_list=[{"name": "Lote provisional", "location_id": padre, "usage": "internal",
                                                                          "company_id": 1, "barcode": LOTE_BARCODE}])
            x = [{"id": nid[0] if isinstance(nid, list) else nid}]
        UBIC["LOTE"]["loc"] = x[0]["id"]
        return UBIC["LOTE"]["loc"]

    def U_(k):
        if k == "LOTE":
            lote_loc()
        return UBIC[k]

    etq = {"t": 0, "m": {}}

    def etiquetas(k):
        """Códigos de barras de la etiqueta de cada ubicación (los de Odoo, aunque los renombréis: AC-STOCK, T1-STOCK…)."""
        if time.time() - etq["t"] > 900:
            try:
                ls = odoo.search_read("stock.location", [("id", "in", [v["loc"] for v in UBIC.values() if v["loc"]])], ["barcode"], TODAS)
                etq["m"] = {x["id"]: (x.get("barcode") or "").upper() for x in ls}
                etq["t"] = time.time()
            except Exception:
                pass
        base = {"AC": {"WHSTOCK", "AC-STOCK", "ACSTOCK"}, "T1": {"T1STOCK", "T1-STOCK"}, "T2": {"T2STOCK", "T2-STOCK"}, "VP": {"LOGIPSTOCK", "VP-STOCK", "VPSTOCK"},
                "LOTE": {LOTE_BARCODE}}[k]
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
            invs = [dict(x) for x in c.execute(f"""SELECT i.id, i.ubic, i.nombre, i.estado, i.usuario, i.cuando, i.aplicado,
                  (SELECT COUNT(*) FROM recuentos r WHERE r.inv_id=i.id) zonas,
                  (SELECT COUNT(*) FROM recuentos r WHERE r.inv_id=i.id AND r.estado='abierto') abiertas
                  FROM inventarios i WHERE i.ubic IN ({','.join('?' * len(us))}) AND (i.estado='abierto' OR i.aplicado > ?) ORDER BY i.id DESC""",
                                                (*us, time.time() - 30 * 86400)).fetchall()]
        for r in rs:
            r.pop("resultado", None)
            r["ubic_nombre"] = UBIC[r["ubic"]]["nombre"]
        for i in invs:
            i["ubic_nombre"] = UBIC[i["ubic"]]["nombre"]
        return {"ubicaciones": [{"codigo": k, "nombre": UBIC[k]["nombre"]} for k in us], "recuentos": rs, "inventarios": invs, "admin": es_admin(u),
                "usuario": nombre_usuario(u)}

    def inv_abierto(ub):
        with con() as c:
            x = c.execute("SELECT * FROM inventarios WHERE ubic=? AND estado='abierto' ORDER BY id DESC LIMIT 1", (ub,)).fetchone()
        return dict(x) if x else None

    @app.post("/api/inv/nuevo")
    async def nuevo(request: Request):
        u = current_user(request)
        b = await request.json()
        ub = (b.get("ubic") or "").upper()
        if ub not in puede(u):
            raise HTTPException(400, "No puedes contar en esa ubicación")
        nom = (b.get("nombre") or "").strip()[:80] or f"Recuento {dt.date.today().strftime('%d/%m/%Y')}"
        inv = inv_abierto(ub)   # si hay un inventario general abierto en esa ubicación, la zona va dentro
        if not inv and not es_admin(u):   # las tiendas solo cuentan cuando el administrador abre el inventario
            raise HTTPException(400, f"No hay ningún inventario abierto en {UBIC[ub]['nombre']}. Lo abre el administrador los días de recuento.")
        with lock, con() as c:
            rid = c.execute("INSERT INTO recuentos (ubic, nombre, estado, usuario, cuando, inv_id) VALUES (?,?,?,?,?,?)",
                            (ub, nom, "abierto", u, time.time(), inv["id"] if inv else None)).lastrowid
        return {"id": rid, "inv_id": inv["id"] if inv else None}

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
            if "descripcion" in b and not x["product_id"]:   # código que no conocemos: qué es (para darlo de alta luego)
                c.execute("UPDATE lineas SET nombre=? WHERE id=?", ((b.get("descripcion") or "").strip()[:200], lid))
                return {"ok": True}
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
        if cant < 0:   # 0 vale: «lo he buscado y no hay» (p. ej. los que están en negativo)
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
        U = guard(lambda: U_(r["ubic"]))
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
        if r.get("inv_id"):
            raise HTTPException(400, "Esta zona es parte de un inventario general: se aplica todo junto desde el inventario")
        U = guard(lambda: U_(r["ubic"]))
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

    # ---------- inventario general por zonas: se cuenta poco a poco mientras se vende y se aplica todo junto ----------
    POS = {"AC": 1, "T1": 2, "T2": 4, "VP": 5}   # TPV de cada ubicación

    def utc(t):
        return dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")

    def inventario(iid, u):
        with con() as c:
            i = c.execute("SELECT * FROM inventarios WHERE id=?", (iid,)).fetchone()
        if not i or i["ubic"] not in puede(u):
            raise HTTPException(404, "No encuentro ese inventario")
        return dict(i)

    @app.post("/api/inv/inventario/nuevo")
    async def inv_nuevo(request: Request):
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "El inventario lo abre el administrador")
        b = await request.json()
        ub = (b.get("ubic") or "").upper()
        if ub not in puede(u):
            raise HTTPException(400, "No puedes contar en esa ubicación")
        if inv_abierto(ub):
            raise HTTPException(400, f"Ya hay un inventario general abierto en {UBIC[ub]['nombre']}")
        nom = (b.get("nombre") or "").strip()[:80] or f"Inventario {UBIC[ub]['nombre']} {dt.date.today().strftime('%m/%Y')}"
        with lock, con() as c:
            iid = c.execute("INSERT INTO inventarios (ubic, nombre, estado, usuario, cuando) VALUES (?,?,?,?,?)", (ub, nom, "abierto", u, time.time())).lastrowid
        return {"id": iid}

    def calc_inventario(i):
        """Suma todas las zonas y resta lo que ha salido (ventas del TPV, albaranes, traspasos) desde que se contó cada artículo."""
        U = guard(lambda: U_(i["ubic"]))
        with con() as c:
            zonas = [dict(x) for x in c.execute("SELECT * FROM recuentos WHERE inv_id=? ORDER BY id", (i["id"],)).fetchall()]
            ls = [dict(x) for x in c.execute("""SELECT l.*, r.nombre zona, r.cuando zona_t FROM lineas l JOIN recuentos r ON r.id=l.rid
                                                WHERE r.inv_id=?""", (i["id"],)).fetchall()]
        arts, sin = {}, []
        for l in ls:
            if not l["product_id"]:
                sin.append(l)
                continue
            a = arts.setdefault(l["product_id"], {"product_id": l["product_id"], "nombre": l["nombre"], "ref": l["ref"], "zonas": [], "contado": 0, "t": l["zona_t"]})
            a["zonas"].append({"zona": l["zona"], "cant": l["cant"]})
            a["contado"] += l["cant"]
            a["t"] = min(a["t"], l["zona_t"])
        pids = list(arts)
        salidas = {p: 0.0 for p in pids}
        if pids:
            tmin = utc(min(a["t"] for a in arts.values()))
            # 1) ventas del TPV por fecha de venta (los albaranes del TPV llevan la fecha del cierre de caja: esos se saltan abajo)
            pos = odoo.search_read("pos.order.line", [("product_id", "in", pids), ("order_id.config_id", "=", POS[i["ubic"]]),
                                                      ("order_id.date_order", ">", tmin), ("order_id.state", "not in", ["draft", "cancel"])],
                                   ["product_id", "qty", "order_id", "create_date"], [U["empresa"]], limit=100000) if POS.get(i["ubic"]) else []
            fechas = {}
            if pos:
                oids = list({x["order_id"][0] for x in pos})
                fechas = {o["id"]: o["date_order"] for o in odoo.search_read("pos.order", [("id", "in", oids)], ["date_order"], [U["empresa"]], limit=100000)}
            for x in pos:
                p = x["product_id"][0]
                if fechas.get(x["order_id"][0], "") > utc(arts[p]["t"]):
                    salidas[p] += x["qty"]
            # 2) el resto de movimientos hechos (albaranes a clientes, traspasos, recepciones) desde que se contó
            locs = {l["id"] for l in odoo.search_read("stock.location", [("id", "child_of", U["loc"])], ["id"], TODAS, limit=5000)}
            mls = odoo.search_read("stock.move.line", [("product_id", "in", pids), ("state", "=", "done"), ("date", ">", tmin),
                                                       "|", ("location_id", "child_of", U["loc"]), ("location_dest_id", "child_of", U["loc"])],
                                   ["product_id", "quantity", "location_id", "location_dest_id", "date", "picking_id"], [U["empresa"]], limit=100000)
            pk = list({m["picking_id"][0] for m in mls if m.get("picking_id")})
            del_tpv = {p["id"] for p in odoo.search_read("stock.picking", [("id", "in", pk), ("pos_session_id", "!=", False)], ["id"], [U["empresa"]], limit=100000)} if pk else set()
            for m in mls:
                if m.get("picking_id") and m["picking_id"][0] in del_tpv:
                    continue
                p = m["product_id"][0]
                if m["date"] <= utc(arts[p]["t"]):
                    continue
                sale, entra = m["location_id"][0] in locs, m["location_dest_id"][0] in locs
                if sale and not entra:
                    salidas[p] += m["quantity"]
                elif entra and not sale:
                    salidas[p] -= m["quantity"]
        st = teorico(U, pids)
        out = []
        for p, a in arts.items():
            t = round(sum(x["quantity"] for x in st.get(p, [])), 3)
            final = round(a["contado"] - salidas[p], 3)
            out.append({**a, "salido": round(salidas[p], 3), "final": final, "teorico": t, "dif": round(final - t, 3)})
        out.sort(key=lambda x: (x["nombre"] or "").lower())
        # artículos en negativo en esa ubicación (vendidos sin stock): hay que buscarlos
        neg = odoo.search_read("stock.quant", [("location_id", "child_of", U["loc"]), ("quantity", "<", 0)], ["product_id", "quantity"], [U["empresa"]], limit=5000)
        negs = {}
        for q in neg:
            negs.setdefault(q["product_id"][0], {"product_id": q["product_id"][0], "nombre": q["product_id"][1], "cant": 0})["cant"] += q["quantity"]
        negativos = sorted(({**v, "contado": v["product_id"] in arts} for v in negs.values()), key=lambda x: x["nombre"].lower())
        return {"zonas": zonas, "lineas": out, "sin_articulo": sin, "negativos": negativos}

    @app.get("/api/inv/inventario/{iid}")
    def inv_ver(iid: int, request: Request):
        u = current_user(request)
        i = inventario(iid, u)

        def run():
            d = calc_inventario(i)
            for z in d["zonas"]:
                z.pop("resultado", None)
            ls = d["lineas"]
            return {"inventario": {k: v for k, v in i.items() if k != "resultado"}, "ubic_nombre": UBIC[i["ubic"]]["nombre"], **d,
                    "resultado": json.loads(i["resultado"]) if i.get("resultado") else None, "admin": es_admin(u),
                    "resumen": {"refs": len(ls), "con_dif": sum(1 for x in ls if abs(x["dif"]) > 0.0001),
                                "abiertas": sum(1 for z in d["zonas"] if z["estado"] == "abierto")}}
        return guard(run)

    @app.post("/api/inv/inventario/{iid}/aplicar")
    async def inv_aplicar(iid: int, request: Request):
        """Solo administrador: deja en Odoo el stock de cada artículo contado = suma de zonas − lo salido desde que se contó."""
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "Solo el administrador aplica el inventario en Odoo")
        i = inventario(iid, u)
        if i["estado"] != "abierto":
            raise HTTPException(400, "Este inventario ya está aplicado")
        U = guard(lambda: U_(i["ubic"]))
        E = U["empresa"]

        def run():
            ses = odoo.search_read("pos.session", [("config_id", "=", POS[i["ubic"]]), ("state", "!=", "closed")], ["name"], [E], limit=1) if POS.get(i["ubic"]) else []
            if ses:
                raise HTTPException(400, f"La caja de {U['nombre']} está abierta ({ses[0]['name']}): aplícalo con la caja cerrada, si no se descontarían dos veces las ventas de hoy")
            d = calc_inventario(i)
            if any(z["estado"] == "abierto" for z in d["zonas"]):
                raise HTTPException(400, "Hay zonas sin terminar de contar")
            st = teorico(U, [l["product_id"] for l in d["lineas"]])
            ctx = {"inventory_mode": True, "inventory_name": f"Inventario panel #{iid} {i['nombre']}"[:120]}
            qids, cambios, errores = [], [], []
            for l in d["lineas"]:
                if abs(l["dif"]) < 0.0001:
                    continue
                qs = st.get(l["product_id"], [])
                try:
                    principal = [x for x in qs if x["location_id"][0] == U["loc"] and not x.get("lot_id")]
                    otros = sum(x["quantity"] for x in qs if not principal or x["id"] != principal[0]["id"])
                    objetivo = l["final"] - otros
                    if principal:
                        odoo.call("stock.quant", "write", [E], ids=[principal[0]["id"]], vals={"inventory_quantity": objetivo}, context=dict(ctx))
                        qid = principal[0]["id"]
                    else:
                        qid = odoo.call("stock.quant", "create", [E], vals_list=[{"product_id": l["product_id"], "location_id": U["loc"],
                                                                                  "inventory_quantity": objetivo}], context=dict(ctx))
                        qid = qid[0] if isinstance(qid, list) else qid
                    qids.append(qid)
                    cambios.append({"producto": l["nombre"], "antes": l["teorico"], "ahora": l["final"]})
                except Exception as e:
                    errores.append({"producto": l["nombre"], "error": str(e)[:200]})
            for k in range(0, len(qids), 100):
                odoo.call("stock.quant", "action_apply_inventory", [E], ids=qids[k:k + 100], context=dict(ctx))
            res = {"ajustados": len(cambios), "sin_cambio": len(d["lineas"]) - len(cambios) - len(errores), "errores": errores, "cambios": cambios}
            with lock, con() as c:
                if not errores:
                    c.execute("UPDATE inventarios SET estado='aplicado', aplicado_por=?, aplicado=?, resultado=? WHERE id=?", (u, time.time(), json.dumps(res, ensure_ascii=False), iid))
                    c.execute("UPDATE recuentos SET estado='aplicado', aplicado_por=?, aplicado=? WHERE inv_id=?", (u, time.time(), iid))
                else:
                    c.execute("UPDATE inventarios SET resultado=? WHERE id=?", (json.dumps(res, ensure_ascii=False), iid))
            return res
        return guard(run)

    @app.post("/api/inv/linea/{lid}/crear_articulo")
    async def crear_articulo(lid: int, request: Request):
        """Solo admin: da de alta en Odoo un código que no conocíamos (sin stock, sin publicar en la web) y lo asigna a la línea.
        {nombre, pvp, coste}"""
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "Solo el administrador da de alta artículos")
        b = await request.json()
        nom = (b.get("nombre") or "").strip()
        if len(nom) < 3:
            raise HTTPException(400, "Pon el nombre del artículo")
        with con() as c:
            x = c.execute("SELECT * FROM lineas WHERE id=?", (lid,)).fetchone()
        if not x or x["product_id"]:
            raise HTTPException(400, "Esa línea ya tiene artículo")

        def run():
            vals = {"name": nom.upper(), "list_price": float(b.get("pvp") or 0), "standard_price": float(b.get("coste") or 0),
                    "is_storable": True, "sale_ok": True, "purchase_ok": True, "is_published": False}
            cod = (x["codigo"] or "").strip()
            if cod and not odoo.search_read("product.product", [("barcode", "=", cod)], ["id"], TODAS, limit=1):
                vals["barcode"] = cod
            tid = odoo.call("product.template", "create", [1], vals_list=[vals])
            tid = tid[0] if isinstance(tid, list) else tid
            pp = odoo.search_read("product.product", [("product_tmpl_id", "=", tid)], ["id", "display_name", "default_code"], [1], limit=1)[0]
            with lock, con() as c:
                c.execute("UPDATE lineas SET product_id=?, nombre=?, ref=? WHERE id=?", (pp["id"], pp["display_name"], pp.get("default_code") or "", lid))
            cache["t"] = 0
            sumado = sumar_si_aplicado(x, pp["id"])
            return {"ok": True, "product_id": pp["id"], "nombre": pp["display_name"], "sumado_stock": sumado}
        return guard(run)

    def sumar_si_aplicado(x, pid):
        """Si el recuento de esa línea ya se aplicó en Odoo (el código se quedó pendiente), al identificarlo se suma su cantidad al stock de esa ubicación."""
        with con() as c:
            r = c.execute("SELECT * FROM recuentos WHERE id=?", (x["rid"],)).fetchone()
        if not r or r["estado"] != "aplicado" or not x["cant"]:
            return 0
        U = U_(r["ubic"])
        E = U["empresa"]
        ctx = {"inventory_mode": True, "inventory_name": f"Recuento panel #{r['id']}: código {x['codigo']} identificado"[:120]}
        q = odoo.search_read("stock.quant", [("product_id", "=", pid), ("location_id", "=", U["loc"]), ("lot_id", "=", False)], ["id", "quantity"], [E], limit=1)
        if q:
            odoo.call("stock.quant", "write", [E], ids=[q[0]["id"]], vals={"inventory_quantity": q[0]["quantity"] + x["cant"]}, context=dict(ctx))
            qid = q[0]["id"]
        else:
            qid = odoo.call("stock.quant", "create", [E], vals_list=[{"product_id": pid, "location_id": U["loc"], "inventory_quantity": x["cant"]}], context=dict(ctx))
            qid = qid[0] if isinstance(qid, list) else qid
        odoo.call("stock.quant", "action_apply_inventory", [E], ids=[qid], context=dict(ctx))
        return x["cant"]

    @app.post("/api/inv/linea/{lid}/asignar")
    async def asignar(lid: int, request: Request):
        """Solo admin: une un código pendiente a un artículo que ya existe (aunque el recuento ya esté aplicado). {product_id, poner_ean}"""
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "Solo el administrador")
        b = await request.json()
        pid = int(b.get("product_id") or 0)
        with con() as c:
            x = c.execute("SELECT * FROM lineas WHERE id=?", (lid,)).fetchone()
        if not x or x["product_id"]:
            raise HTTPException(400, "Esa línea ya tiene artículo")
        x = dict(x)

        def run():
            pr = odoo.search_read("product.product", [("id", "=", pid)], ["display_name", "default_code", "barcode"], TODAS, limit=1)
            if not pr:
                raise HTTPException(404, "No encuentro el artículo")
            if b.get("poner_ean") and not pr[0].get("barcode") and (x["codigo"] or "").strip() \
                    and not odoo.search_read("product.product", [("barcode", "=", x["codigo"].strip())], ["id"], TODAS, limit=1):
                odoo.call("product.product", "write", TODAS, ids=[pid], vals={"barcode": x["codigo"].strip()})
                cache["t"] = 0
            with lock, con() as c:
                c.execute("UPDATE lineas SET product_id=?, nombre=?, ref=? WHERE id=?", (pid, pr[0]["display_name"], pr[0].get("default_code") or "", lid))
            return {"ok": True, "sumado_stock": sumar_si_aplicado(x, pid)}
        return guard(run)

    def pendientes():
        with con() as c:
            return [dict(x) for x in c.execute("""SELECT l.id, l.codigo, l.nombre descripcion, l.cant, l.usuario, l.cuando, r.id rid, r.nombre zona, r.ubic, r.estado,
                                                  i.nombre inventario FROM lineas l JOIN recuentos r ON r.id=l.rid LEFT JOIN inventarios i ON i.id=r.inv_id
                                                  WHERE l.product_id IS NULL ORDER BY l.cuando DESC""").fetchall()]

    @app.get("/api/inv/pendientes")
    def ver_pendientes(request: Request, formato: str = ""):
        """Códigos contados que no tenemos en Odoo: para buscarlos luego (proveedor, fabricante) y darlos de alta o unirlos a otro artículo."""
        u = current_user(request)
        us = puede(u)
        ps = [p for p in pendientes() if p["ubic"] in us]
        for p in ps:
            p["ubic_nombre"] = UBIC[p["ubic"]]["nombre"]
        if formato != "csv":
            return {"pendientes": ps, "admin": es_admin(u)}
        f = io.StringIO()
        w = csv.writer(f, delimiter=";")
        w.writerow(["Código de barras", "Qué es (descripción)", "Unidades", "Dónde", "Zona", "Inventario", "Contado por", "Fecha",
                    "Proveedor / fabricante", "Nombre correcto", "PVP", "Coste", "Unir a la referencia"])
        for p in ps:
            w.writerow([p["codigo"], p["descripcion"], str(p["cant"]).replace(".", ","), p["ubic_nombre"], p["zona"], p["inventario"] or "",
                        p["usuario"], dt.datetime.fromtimestamp(p["cuando"]).strftime("%d/%m/%Y"), "", "", "", "", ""])
        return Response("﻿" + f.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="codigos_por_identificar.csv"'})

    # ---------- lote provisional: lo que hay sin repartir y repartirlo a almacén / tiendas ----------
    DESTINOS = ["AC", "T1", "T2"]   # Distriplus (Logipaper es otra empresa: se le pasa con pedido entre empresas)

    @app.get("/api/inv/lote")
    def lote_ver(request: Request):
        u = current_user(request)
        if "LOTE" not in puede(u):
            raise HTTPException(403, "No puedes ver el lote")

        def run():
            L = lote_loc()
            qs = odoo.search_read("stock.quant", [("location_id", "child_of", L), ("quantity", ">", 0)], ["product_id", "quantity"], [1], limit=20000)
            arts = {}
            for q in qs:
                a = arts.setdefault(q["product_id"][0], {"product_id": q["product_id"][0], "nombre": q["product_id"][1], "lote": 0})
                a["lote"] += q["quantity"]
            pids = list(arts)
            if pids:
                for k in DESTINOS:
                    for q in odoo.search_read("stock.quant", [("product_id", "in", pids), ("location_id", "child_of", UBIC[k]["loc"])], ["product_id", "quantity"], [1], limit=50000):
                        arts[q["product_id"][0]][k] = arts[q["product_id"][0]].get(k, 0) + q["quantity"]
                for p in odoo.search_read("product.product", [("id", "in", pids)], ["default_code", "list_price", "standard_price"], [1], limit=20000):
                    arts[p["id"]].update(ref=p.get("default_code") or "", pvp=p.get("list_price") or 0, coste=p.get("standard_price") or 0)
            return {"destinos": [{"codigo": k, "nombre": UBIC[k]["nombre"]} for k in DESTINOS], "admin": es_admin(u),
                    "lineas": sorted(arts.values(), key=lambda a: a["nombre"].lower())}
        return guard(run)

    @app.post("/api/inv/lote/repartir")
    async def lote_repartir(request: Request):
        """Solo admin: {reparto: [{product_id, AC: n, T1: n, T2: n}]} → un traspaso interno validado por destino desde el lote."""
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "Solo el administrador reparte el lote")
        b = await request.json()

        def run():
            L = lote_loc()
            hay = {}
            for q in odoo.search_read("stock.quant", [("location_id", "child_of", L), ("quantity", ">", 0)], ["product_id", "quantity"], [1], limit=20000):
                hay[q["product_id"][0]] = hay.get(q["product_id"][0], 0) + q["quantity"]
            por_dest = {k: [] for k in DESTINOS}
            for r in b.get("reparto") or []:
                pid = int(r.get("product_id") or 0)
                tot = sum(float(r.get(k) or 0) for k in DESTINOS)
                if tot <= 0:
                    continue
                if tot > hay.get(pid, 0) + 0.0001:
                    raise HTTPException(400, f"Del artículo {pid} repartes {tot} y en el lote hay {hay.get(pid, 0)}")
                for k in DESTINOS:
                    q = float(r.get(k) or 0)
                    if q > 0:
                        por_dest[k].append((pid, q))
            tipo = odoo.search_read("stock.picking.type", [("code", "=", "internal"), ("company_id", "=", 1)], ["id"], [1], limit=1)
            if not tipo:
                raise HTTPException(500, "No encuentro el tipo de operación de traspaso interno en Distriplus")
            hechos = []
            for k, ls in por_dest.items():
                if not ls:
                    continue
                dest = UBIC[k]["loc"]
                pid_ = odoo.call("stock.picking", "create", [1], vals_list=[{
                    "picking_type_id": tipo[0]["id"], "location_id": L, "location_dest_id": dest, "origin": "Reparto lote provisional",
                    "move_ids": [(0, 0, {"product_id": p, "product_uom_qty": q, "location_id": L, "location_dest_id": dest, "name": "Reparto lote"}) for p, q in ls]}])
                pk = pid_[0] if isinstance(pid_, list) else pid_
                odoo.call("stock.picking", "action_confirm", [1], ids=[pk])
                odoo.call("stock.picking", "action_assign", [1], ids=[pk])
                for m in odoo.search_read("stock.move", [("picking_id", "=", pk)], ["id", "product_uom_qty"], [1], limit=5000):
                    odoo.call("stock.move", "write", [1], ids=[m["id"]], vals={"quantity": m["product_uom_qty"], "picked": True})
                odoo.call("stock.picking", "button_validate", [1], ids=[pk], context={"skip_backorder": True, "skip_sms": True})
                nm = odoo.search_read("stock.picking", [("id", "=", pk)], ["name", "state"], [1], limit=1)[0]
                hechos.append({"destino": UBIC[k]["nombre"], "albaran": nm["name"], "estado": nm["state"], "articulos": len(ls)})
            return {"ok": True, "traspasos": hechos}
        return guard(run)

    # ---------- artículos nuevos sin código de barras: se les pone escaneándolos con la PDA ----------
    @app.get("/api/inv/sin_ean")
    def sin_ean(request: Request, dias: int = 30):
        current_user(request)
        desde = (dt.datetime.utcnow() - dt.timedelta(days=max(1, min(dias, 365)))).strftime("%Y-%m-%d %H:%M:%S")

        def run():
            dom = [("create_date", ">=", desde), ("barcode", "=", False), ("sale_ok", "=", True), ("type", "=", "consu")]
            ps = odoo.search_read("product.product", dom, ["display_name", "default_code", "seller_ids", "create_date"], TODAS, limit=500, order="create_date desc")
            sis = {}
            ids = [i for p in ps for i in p.get("seller_ids") or []]
            if ids:
                for x in odoo.search_read("product.supplierinfo", [("id", "in", ids)], ["partner_id", "product_code"], TODAS, limit=2000):
                    sis[x["id"]] = x
            out = []
            for p in ps:
                s0 = next((sis[i] for i in p.get("seller_ids") or [] if i in sis), None)
                out.append({"id": p["id"], "nombre": p["display_name"], "ref": p.get("default_code") or "",
                            "proveedor": s0["partner_id"][1] if s0 and s0.get("partner_id") else "", "cod_prov": (s0 or {}).get("product_code") or "",
                            "fecha": (p.get("create_date") or "")[:10]})
            return out
        return guard(run)

    @app.post("/api/inv/poner_ean")
    async def poner_ean(request: Request):
        current_user(request)
        b = await request.json()
        pid = int(b.get("product_id") or 0)
        ean = re.sub(r"\s", "", str(b.get("ean") or ""))
        if not pid or len(ean) < 6:
            raise HTTPException(400, "Escanea el código de barras del artículo")

        def run():
            otro = odoo.search_read("product.product", [("barcode", "=", ean), ("id", "!=", pid)], ["display_name"], TODAS, limit=1)
            if otro:
                raise HTTPException(400, f"Ese código ya lo tiene {otro[0]['display_name']}: ¿es el mismo artículo duplicado? Avisad a Juan")
            p = odoo.search_read("product.product", [("id", "=", pid)], ["display_name", "barcode"], TODAS)
            if not p:
                raise HTTPException(404, "No encuentro el artículo")
            if p[0].get("barcode"):
                raise HTTPException(400, f"{p[0]['display_name']} ya tiene código: {p[0]['barcode']}")
            odoo.call("product.product", "write", [1], ids=[pid], vals={"barcode": ean})
            cache["t"] = 0
            return {"ok": True, "nombre": p[0]["display_name"], "ean": ean}
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
