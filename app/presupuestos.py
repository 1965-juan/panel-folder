"""Presupuestador: almacén de documentos (presupuestos, artículos, ajustes), fotos,
y clientes/artículos de Odoo en directo para el buscador."""
import base64
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import Request, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, RedirectResponse, Response

COLLS = {"presupuestos", "articulos", "config"}
PRICELIST_ID = 16  # tarifa "Folder Castellón" (escalados)
CACHE_TTL = 30 * 60


def setup(app, *, data_dir: Path, static_dir: Path, odoo, current_user, all_company_ids):
    db_path = data_dir / "presupuestos.db"
    blobs = data_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def con():
        c = sqlite3.connect(db_path)
        c.execute("CREATE TABLE IF NOT EXISTS docs (coll TEXT, id TEXT, data TEXT, PRIMARY KEY (coll, id))")
        return c

    # ---------- importación: zip (o carpeta descomprimida) del presupuestador anterior ----------
    def importar_de(leer, nombres):
        """leer(nombre) -> bytes; nombres: lista de rutas dentro del paquete."""
        docs = json.loads(leer("docs.json"))
        n = 0
        with lock, con() as c:
            for coll, items in docs.items():
                if coll not in COLLS:
                    continue
                for did, data in items.items():
                    if not c.execute("SELECT 1 FROM docs WHERE coll=? AND id=?", (coll, did)).fetchone():
                        c.execute("INSERT INTO docs VALUES (?,?,?)", (coll, did, json.dumps(data, ensure_ascii=False)))
                        n += 1
        fotos = 0
        for name in nombres:
            m = re.fullmatch(r"(?:.*/)?blobs/([0-9a-f]{32})\.(png|jpg|jpeg|webp|gif)", name)
            if m and not (blobs / m.group(1)).exists():
                (blobs / m.group(1)).write_bytes(leer(name))
                (blobs / (m.group(1) + ".type")).write_text("image/" + m.group(2).replace("jpg", "jpeg"))
                fotos += 1
        return n, fotos

    def importar_zip(z):
        import zipfile
        with zipfile.ZipFile(z) as zf:
            nombres = zf.namelist()
            raiz = next((x[:-len("docs.json")] for x in nombres if x.endswith("docs.json") and "__MACOSX" not in x), None)
            if raiz is None:
                raise ValueError("El zip no tiene docs.json")
            return importar_de(lambda nm: zf.read(nm if nm != "docs.json" else raiz + "docs.json"),
                               [x for x in nombres if "__MACOSX" not in x])

    def importar():
        """Al arrancar: busca en datos/ cualquier zip o carpeta del presupuestador sin importar todavía."""
        candidatos = [p for p in data_dir.glob("*resupuestador*.zip") if ".hecho" not in p.name]
        for z in candidatos:
            try:
                n, f = importar_zip(z)
                z.rename(z.with_name(z.stem + ".hecho.zip"))
                print(f"[presupuestos] {z.name}: importados {n} documentos y {f} fotos", flush=True)
            except Exception as e:
                print("[presupuestos] error importando", z.name, e, flush=True)
        for d in data_dir.glob("*resupuestador*"):
            if d.is_dir() and (d / "docs.json").exists() and not (d / ".hecho").exists():
                try:
                    files = [str(x.relative_to(d)) for x in d.rglob("*") if x.is_file()]
                    n, f = importar_de(lambda nm: (d / nm).read_bytes(), files)
                    (d / ".hecho").write_text("ok")
                    print(f"[presupuestos] carpeta {d.name}: importados {n} documentos y {f} fotos", flush=True)
                except Exception as e:
                    print("[presupuestos] error importando carpeta", d.name, e, flush=True)

    importar()

    @app.post("/api/presupuestos/importar")
    async def importar_subido(request: Request, archivo: UploadFile = File(...)):
        current_user(request)
        import io
        datos = await archivo.read()
        try:
            n, f = importar_zip(io.BytesIO(datos))
        except Exception as e:
            raise HTTPException(400, f"No se pudo leer el archivo: {e}")
        (data_dir / "importar_presupuestador.subido.hecho.zip").write_bytes(datos)
        return {"ok": True, "documentos": n, "fotos": f}

    @app.get("/presupuestos/importar")
    def importar_pagina(request: Request):
        if not request.session.get("user"):
            return RedirectResponse("/entrar")
        return Response("""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Recuperar presupuestos</title><style>body{font:15px -apple-system,Segoe UI,sans-serif;background:#3A1F73;display:grid;place-items:center;min-height:100vh;margin:0}
.c{background:#fff;border-radius:14px;padding:28px;max-width:440px;display:flex;flex-direction:column;gap:14px}h2{margin:0;color:#3A1F73}
button{background:#F39A2B;color:#fff;border:0;border-radius:8px;padding:11px;font-weight:700;font-size:15px;cursor:pointer}#r{font-weight:600}</style>
<div class=c><h2>Recuperar presupuestos anteriores</h2><p>Elige el archivo <b>importar_presupuestador.zip</b> (está en Descargas).
Solo añade lo que falta: no borra ni cambia nada de lo que ya tienes.</p>
<input type=file id=f accept=".zip"><button id=b>Cargar</button><p id=r></p><a href="/presupuestos">← Ir a Presupuestos</a></div>
<script>b.onclick=async()=>{if(!f.files[0])return r.textContent='Elige el archivo primero';r.textContent='Cargando…';
const fd=new FormData();fd.append('archivo',f.files[0]);const x=await fetch('/api/presupuestos/importar',{method:'POST',body:fd});const j=await x.json();
r.textContent=x.ok?`Hecho: ${j.documentos} documentos y ${j.fotos} fotos recuperados.`:'Error: '+(j.detail||x.status)}</script>""", media_type="text/html")

    def check(coll):
        if coll not in COLLS:
            raise HTTPException(404, "Colección desconocida")

    # ---------- página ----------
    @app.get("/presupuestos")
    def page(request: Request):
        if not request.session.get("user"):
            return RedirectResponse("/entrar")
        return FileResponse(static_dir / "presupuestos.html")

    # ---------- almacén ----------
    @app.get("/api/store/{coll}")
    def list_docs(request: Request, coll: str):
        current_user(request)
        if coll == "clientes_odoo":
            return [{"id": "odoo", "data": clientes_odoo()}]
        if coll == "productos_odoo":
            return [{"id": "odoo", "data": {"p": productos_odoo()}}]
        check(coll)
        with con() as c:
            rows = c.execute("SELECT id, data FROM docs WHERE coll=?", (coll,)).fetchall()
        return [{"id": i, "data": json.loads(d)} for i, d in rows]

    @app.get("/api/store/{coll}/{did}")
    def get_doc(request: Request, coll: str, did: str):
        current_user(request)
        check(coll)
        with con() as c:
            r = c.execute("SELECT data FROM docs WHERE coll=? AND id=?", (coll, did)).fetchone()
        return {"exists": bool(r), "data": json.loads(r[0]) if r else None}

    @app.put("/api/store/{coll}/{did}")
    async def set_doc(request: Request, coll: str, did: str):
        current_user(request)
        check(coll)
        data = await request.json()
        with lock, con() as c:
            c.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?)", (coll, did, json.dumps(data, ensure_ascii=False)))
        return {"ok": True}

    @app.post("/api/store/{coll}")
    async def add_doc(request: Request, coll: str):
        current_user(request)
        check(coll)
        data = await request.json()
        did = secrets.token_hex(10)
        with lock, con() as c:
            c.execute("INSERT INTO docs VALUES (?,?,?)", (coll, did, json.dumps(data, ensure_ascii=False)))
        return {"id": did}

    @app.delete("/api/store/{coll}/{did}")
    def del_doc(request: Request, coll: str, did: str):
        current_user(request)
        check(coll)
        with lock, con() as c:
            c.execute("DELETE FROM docs WHERE coll=? AND id=?", (coll, did))
        return {"ok": True}

    # ---------- fotos ----------
    @app.post("/api/blob")
    async def upload(request: Request, file: UploadFile = File(...)):
        current_user(request)
        if not (file.content_type or "").startswith("image/"):
            raise HTTPException(400, "Solo imágenes")
        content = await file.read()
        if len(content) > 15 * 1024 * 1024:
            raise HTTPException(400, "La imagen es demasiado grande")
        bid = secrets.token_hex(16)
        (blobs / bid).write_bytes(content)
        (blobs / (bid + ".type")).write_text(file.content_type)
        return {"id": bid}

    @app.get("/_blob/{bid}")
    def blob(request: Request, bid: str):
        current_user(request)
        m = re.fullmatch(r"odoo-(\d+)", bid)
        if m:
            f = blobs / f"odoo-{m.group(1)}"
            if not f.exists() or time.time() - f.stat().st_mtime > 7 * 86400:
                rows = odoo.search_read("product.template", [("id", "=", int(m.group(1)))], ["image_512"],
                                        all_company_ids())
                img = rows[0].get("image_512") if rows else None
                if not img:
                    raise HTTPException(404, "Sin foto en Odoo")
                f.write_bytes(base64.b64decode(img))
            raw = f.read_bytes()
            ctype = "image/png" if raw[:4] == b"\x89PNG" else "image/jpeg"
            return Response(raw, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})
        if not re.fullmatch(r"[0-9a-f]{16,64}", bid) or not (blobs / bid).exists():
            raise HTTPException(404, "No existe")
        t = blobs / (bid + ".type")
        return FileResponse(blobs / bid, media_type=t.read_text() if t.exists() else "image/png",
                            headers={"Cache-Control": "private, max-age=604800"})

    # ---------- Odoo en directo (con caché) ----------
    cache = {}

    def cached(key, fn):
        hit = cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        val = fn()
        cache[key] = (time.time(), val)
        return val

    @app.post("/api/odoo/refrescar")
    def refrescar(request: Request):
        current_user(request)
        cache.clear()
        return {"ok": True}

    def clientes_odoo():
        def load():
            rows = odoo.search_read(
                "res.partner",
                ["|", ("customer_rank", ">", 0), ("type", "=", "delivery")],
                ["name", "display_name", "vat", "street", "zip", "city", "email", "phone",
                 "parent_id", "type", "is_company"],
                all_company_ids(), limit=20000)
            out = []
            for r in rows:
                parent = r["parent_id"][1] if r.get("parent_id") else ""
                person = not r.get("is_company") and parent
                out.append({
                    "i": r["id"],
                    "n": parent if person else r.get("name") or r.get("display_name") or "",
                    "c": (r.get("name") or "") if person else "",
                    "v": r.get("vat") or "",
                    "m": r.get("email") or "",
                    "d": r.get("street") or "",
                    "p": " ".join(x for x in [r.get("zip") or "", r.get("city") or ""] if x),
                    "f": r.get("phone") or "",
                    "t": "e" if r.get("type") == "delivery" else "",
                })
            return {"clientes": out, "fecha": time.strftime("%Y-%m-%d")}
        return cached("clientes", load)

    def productos_odoo():
        def load():
            cids = all_company_ids()
            prods = odoo.search_read("product.product", [("sale_ok", "=", True)],
                                     ["default_code", "name", "lst_price", "product_tmpl_id"], cids, limit=50000)
            items = odoo.search_read(
                "product.pricelist.item",
                [("pricelist_id", "=", PRICELIST_ID), ("compute_price", "=", "fixed"),
                 ("applied_on", "in", ["1_product", "0_product_variant"])],
                ["product_tmpl_id", "product_id", "min_quantity", "fixed_price"], cids, limit=100000)
            by_tmpl, by_var = {}, {}
            for it in items:
                pair = [max(1, int(it.get("min_quantity") or 0) or 1), round(it.get("fixed_price") or 0, 4)]
                if it.get("product_id"):
                    by_var.setdefault(it["product_id"][0], []).append(pair)
                elif it.get("product_tmpl_id"):
                    by_tmpl.setdefault(it["product_tmpl_id"][0], []).append(pair)
            out = []
            for p in prods:
                tmpl = p["product_tmpl_id"][0] if p.get("product_tmpl_id") else None
                tiers = by_var.get(p["id"]) or by_tmpl.get(tmpl) or []
                tiers = sorted({t[0]: t for t in tiers}.values())
                if tiers and tiers[0][0] > 1:
                    tiers = [[1, round(p.get("lst_price") or 0, 4)]] + tiers
                out.append([p["id"], p.get("default_code") or "", p.get("name") or "",
                            round(p.get("lst_price") or 0, 4), tiers, tmpl])
            return out
        return cached("productos", load)

    # ---------- asistente: hablar con Claude para montar el presupuesto ----------
    import hashlib
    import httpx as _httpx

    clave_f = data_dir / "claude_clave.txt"
    modelo = os.environ.get("PRES_MODEL") or os.environ.get("WA_MODEL") or "claude-sonnet-5-5"
    web = _httpx.Client(timeout=40, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (PanelFolder presupuestos)"})

    def clave():
        if not clave_f.exists():
            return ""
        m = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", clave_f.read_text(errors="ignore"))
        return m[0] if m else ""

    def norm(t):
        import unicodedata
        return unicodedata.normalize("NFD", str(t or "").lower()).encode("ascii", "ignore").decode()

    def guardar_imagen(url):
        r = web.get(url)
        ct = (r.headers.get("content-type") or "").split(";")[0].strip()
        if r.status_code != 200 or not ct.startswith("image/") or len(r.content) > 10_000_000:
            raise ValueError(f"no es una imagen válida ({r.status_code} {ct})")
        bid = hashlib.md5(r.content).hexdigest()
        if not (blobs / bid).exists():
            (blobs / bid).write_bytes(r.content)
            (blobs / (bid + ".type")).write_text(ct)
        return bid

    HERR = [
        {"name": "buscar_clientes", "description": "Busca clientes en Odoo por nombre, NIF, email o teléfono. Devuelve sus datos de facturación.",
         "input_schema": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]}},
        {"name": "poner_cliente", "description": "Pone los datos del cliente en el presupuesto (de Odoo o uno nuevo que no esté dado de alta).",
         "input_schema": {"type": "object", "properties": {k: {"type": "string"} for k in
                          ["nombre", "nif", "direccion", "poblacion", "email", "telefono", "contacto"]}, "required": ["nombre"]}},
        {"name": "buscar_articulos", "description": "Busca artículos en el catálogo de Odoo (con sus precios por tramos) y en los artículos guardados del presupuestador.",
         "input_schema": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]}},
        {"name": "anadir_articulo_odoo", "description": "Añade al presupuesto un artículo de Odoo por su id (de buscar_articulos), con su foto y tramos.",
         "input_schema": {"type": "object", "properties": {"odoo_id": {"type": "integer"}, "cantidad": {"type": "number"}}, "required": ["odoo_id"]}},
        {"name": "anadir_articulo_guardado", "description": "Añade un artículo guardado del presupuestador por su id.",
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "cantidad": {"type": "number"}}, "required": ["id"]}},
        {"name": "anadir_ficha", "description": "Añade una ficha de artículo nuevo (por ejemplo sacado de la web de un proveedor). Las fotos se descargan de las URL de imagen que pases.",
         "input_schema": {"type": "object", "properties": {
             "nombre": {"type": "string"}, "subtitulo": {"type": "string"}, "descripcion": {"type": "string"},
             "ref": {"type": "string"}, "proveedor": {"type": "string"}, "ref_proveedor": {"type": "string"},
             "tramos": {"type": "array", "description": "Precios de venta sin IVA por cantidad mínima", "items": {"type": "object", "properties": {"desde": {"type": "number"}, "precio": {"type": "number"}}}},
             "colores": {"type": "array", "items": {"type": "object", "properties": {"nombre": {"type": "string"}, "hex": {"type": "string"}}}},
             "imagenes": {"type": "array", "items": {"type": "string"}, "description": "URL de las fotos del producto"},
             "cantidad": {"type": "number"}, "coste_notas": {"type": "string"},
             "guardar_en_catalogo": {"type": "boolean", "description": "Guardar también en Artículos para reutilizarlo"}}, "required": ["nombre"]}},
        {"name": "anadir_linea_simple", "description": "Añade una línea de texto con precio (servicio, marcaje, portes…).",
         "input_schema": {"type": "object", "properties": {"descripcion": {"type": "string"}, "detalle": {"type": "string"},
                                                          "cantidad": {"type": "number"}, "precio": {"type": "number"}}, "required": ["descripcion"]}},
        {"name": "cambiar_linea", "description": "Cambia cantidad o precio de una línea por su id; precio fija el precio y quita el automático por tramos.",
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}, "cantidad": {"type": "number"}, "precio": {"type": "number"}}, "required": ["id"]}},
        {"name": "quitar_linea", "description": "Quita una línea del presupuesto por su id.",
         "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
        {"name": "ajustes_presupuesto", "description": "Cambia notas, validez en días o si es un presupuesto de opciones sin total.",
         "input_schema": {"type": "object", "properties": {"notas": {"type": "string"}, "validez_dias": {"type": "integer"}, "sin_total": {"type": "boolean"}}}},
        {"name": "guardar_imagen", "description": "Descarga una foto desde una URL y la guarda; devuelve el id para usarlo en fichas.",
         "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
        {"name": "pasar_a_odoo", "description": "Pasa este presupuesto a Odoo: busca o da de alta el cliente, da de alta los artículos nuevos (con foto y precio) y crea el presupuesto de venta; si confirmar=true lo confirma como pedido. SOLO cuando Juan lo pida expresamente y haya dicho la empresa.",
         "input_schema": {"type": "object", "properties": {"empresa": {"type": "string", "description": "DISTRIPLUS, INGRAF, LOGIPAPER…"},
                                                          "confirmar": {"type": "boolean"}}, "required": ["empresa"]}},
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
        {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 8},
    ]

    def linea_ficha(art, cantidad):
        tr = art.get("tramos") or []
        return {"id": secrets.token_hex(4), "tipo": "ficha", "art": art, "cantidad": cantidad or 1, "precioAuto": True,
                "precio": tr[0]["precio"] if tr else 0, "mostrarTramos": True}

    def ejecutar(q, nombre, a):
        L = q.setdefault("lineas", [])
        if nombre == "buscar_clientes":
            t = norm(a["texto"])
            r = [c for c in clientes_odoo()["clientes"] if t in norm(" ".join(str(c.get(k) or "") for k in ("n", "c", "v", "m", "f", "p")))][:10]
            return r or "Ninguno en Odoo con ese texto"
        if nombre == "poner_cliente":
            q["cliente"] = {k: a.get(k, "") or "" for k in ["nombre", "nif", "direccion", "poblacion", "email", "telefono", "contacto"]}
            return "Cliente puesto"
        if nombre == "buscar_articulos":
            palabras = norm(a["texto"]).split()
            od = [{"odoo_id": p[0], "ref": p[1], "nombre": p[2], "precio": p[3], "tramos": p[4]} for p in productos_odoo()
                  if all(w in norm(p[1] + " " + p[2]) for w in palabras)][:15]
            with con() as c:
                arts = [dict(json.loads(r[1]), id=r[0]) for r in c.execute("SELECT id, data FROM docs WHERE coll='articulos'").fetchall()]
            gu = [{"id": x["id"], "nombre": x.get("nombre"), "ref": x.get("ref"), "proveedor": x.get("proveedor"), "tramos": x.get("tramos")}
                  for x in arts if all(w in norm(" ".join(str(x.get(k) or "") for k in ("nombre", "ref", "proveedor", "descripcion"))) for w in palabras)][:10]
            return {"odoo": od, "guardados": gu}
        if nombre == "anadir_articulo_odoo":
            p = next((x for x in productos_odoo() if x[0] == a["odoo_id"]), None)
            if not p:
                return "No existe ese artículo en Odoo"
            tr = [{"desde": t[0], "precio": t[1]} for t in (p[4] or [])] or [{"desde": 1, "precio": p[3]}]
            art = {"artId": f"odoo-{p[0]}", "nombre": p[2], "ref": p[1], "odooRef": p[1], "origen": "odoo", "tramos": tr,
                   "imagenes": [f"odoo-{p[5]}"] if p[5] else [], "descripcion": "", "subtitulo": "", "proveedor": "", "refProveedor": "", "colores": []}
            L.append(linea_ficha(art, a.get("cantidad")))
            return f"Añadido: {p[2]}"
        if nombre == "anadir_articulo_guardado":
            with con() as c:
                r = c.execute("SELECT data FROM docs WHERE coll='articulos' AND id=?", (a["id"],)).fetchone()
            if not r:
                return "No existe ese artículo guardado"
            art = json.loads(r[0])
            art.pop("creado", None); art.pop("actualizado", None)
            art["artId"] = a["id"]
            L.append(linea_ficha(art, a.get("cantidad")))
            return f"Añadido: {art.get('nombre')}"
        if nombre == "anadir_ficha":
            imgs, fallos = [], []
            for u in (a.get("imagenes") or [])[:6]:
                if re.fullmatch(r"[0-9a-f]{32}", u):
                    imgs.append(u); continue
                try:
                    imgs.append(guardar_imagen(u))
                except Exception as e:
                    fallos.append(f"{u[:80]}: {e}")
            art = {"artId": "ia-" + secrets.token_hex(4), "nombre": a["nombre"], "subtitulo": a.get("subtitulo", ""), "descripcion": a.get("descripcion", ""),
                   "ref": a.get("ref", ""), "proveedor": a.get("proveedor", ""), "refProveedor": a.get("ref_proveedor", ""), "odooRef": "", "origen": "nuevo",
                   "tramos": sorted(a.get("tramos") or [], key=lambda t: t.get("desde", 0)), "colores": a.get("colores") or [], "imagenes": imgs,
                   "costeNotas": a.get("coste_notas", "")}
            if a.get("guardar_en_catalogo"):
                aid = secrets.token_hex(10)
                d = {k: v for k, v in art.items() if k != "artId"}
                d["creado"] = d["actualizado"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                with lock, con() as c:
                    c.execute("INSERT INTO docs VALUES (?,?,?)", ("articulos", aid, json.dumps(d, ensure_ascii=False)))
                art["artId"] = aid
            L.append(linea_ficha(art, a.get("cantidad")))
            return f"Añadida la ficha con {len(imgs)} fotos" + (f". Fotos que no se pudieron bajar: {fallos}" if fallos else "")
        if nombre == "anadir_linea_simple":
            L.append({"id": secrets.token_hex(4), "tipo": "simple", "descripcion": a["descripcion"], "detalle": a.get("detalle", ""),
                      "cantidad": a.get("cantidad") or 1, "precio": a.get("precio") or 0})
            return "Línea añadida"
        if nombre == "cambiar_linea":
            ln = next((x for x in L if x.get("id") == a["id"]), None)
            if not ln:
                return "No existe esa línea"
            if a.get("cantidad") is not None:
                ln["cantidad"] = a["cantidad"]
            if a.get("precio") is not None:
                ln["precio"] = a["precio"]; ln["precioAuto"] = False
            return "Línea cambiada"
        if nombre == "quitar_linea":
            n = len(L)
            q["lineas"] = [x for x in L if x.get("id") != a["id"]]
            return "Quitada" if len(q["lineas"]) < n else "No existe esa línea"
        if nombre == "ajustes_presupuesto":
            if "notas" in a: q["notas"] = a["notas"]
            if "validez_dias" in a: q["validezDias"] = a["validez_dias"]
            if "sin_total" in a: q["sinTotal"] = a["sin_total"]
            return "Hecho"
        if nombre == "guardar_imagen":
            return {"id": guardar_imagen(a["url"])}
        if nombre == "pasar_a_odoo":
            return pasar_a_odoo(q, a.get("empresa", ""), bool(a.get("confirmar")))
        return "Herramienta desconocida"

    def pasar_a_odoo(q, empresa, confirmar):
        if (q.get("odoo") or {}).get("id"):
            return f"Ya está en Odoo como {q['odoo']['nombre']}"
        comps = odoo.search_read("res.company", [], ["name"])
        comp = next((c for c in comps if norm(empresa) and norm(empresa) in norm(c["name"])), None)
        if not comp:
            return "No sé a qué empresa: dime DISTRIPLUS, INGRAF, LOGIPAPER…"
        cid = comp["id"]
        hechos = []
        # cliente
        c = q.get("cliente") or {}
        if not c.get("nombre"):
            return "Falta el cliente"
        pid = None
        if c.get("nif"):
            r = odoo.search_read("res.partner", [("vat", "ilike", re.sub(r"\W", "", c["nif"]))], ["id"], company_ids=[cid], limit=1)
            pid = r[0]["id"] if r else None
        if not pid:
            r = odoo.search_read("res.partner", [("name", "=ilike", c["nombre"])], ["id"], company_ids=[cid], limit=1)
            pid = r[0]["id"] if r else None
        if not pid:
            m = re.match(r"\s*(\d{5})\s*(.*)", c.get("poblacion") or "")
            vals = {"name": c["nombre"], "is_company": True, "street": c.get("direccion") or False, "email": c.get("email") or False,
                    "phone": c.get("telefono") or False, "zip": m.group(1) if m else False,
                    "city": (m.group(2) if m else c.get("poblacion")) or False, "customer_rank": 1}
            if c.get("nif"):
                vals["vat"] = ("ES" + c["nif"]) if re.fullmatch(r"[A-Z0-9]\d{7}[A-Z0-9]", c["nif"].upper()) else c["nif"]
            try:
                pid = odoo.call("res.partner", "create", [cid], vals_list=[vals])
            except Exception:
                vals.pop("vat", None)
                pid = odoo.call("res.partner", "create", [cid], vals_list=[vals])
            pid = pid[0] if isinstance(pid, list) else pid
            hechos.append(f"cliente nuevo {c['nombre']}")
        # artículos
        lineas = []
        generico = None
        for l in q.get("lineas", []):
            qty = l.get("cantidad") or 1
            if l.get("tipo") == "ficha":
                art = l.get("art") or {}
                prod = None
                if art.get("odooRef"):
                    r = odoo.search_read("product.product", [("default_code", "=", art["odooRef"])], ["id"], company_ids=[cid], limit=1)
                    prod = r[0]["id"] if r else None
                if not prod:
                    vals = {"name": art.get("nombre") or "Artículo", "default_code": art.get("ref") or art.get("refProveedor") or False,
                            "list_price": (art.get("tramos") or [{}])[0].get("precio") or l.get("precio") or 0, "sale_ok": True,
                            "purchase_ok": True, "type": "consu", "description_sale": art.get("descripcion") or False}
                    img = next((i for i in art.get("imagenes") or [] if re.fullmatch(r"[0-9a-f]{16,64}", i) and (blobs / i).exists()), None)
                    if img:
                        vals["image_1920"] = base64.b64encode((blobs / img).read_bytes()).decode()
                    tid = odoo.call("product.template", "create", [cid], vals_list=[vals])
                    tid = tid[0] if isinstance(tid, list) else tid
                    prod = odoo.search_read("product.product", [("product_tmpl_id", "=", tid)], ["id"], company_ids=[cid], limit=1)[0]["id"]
                    l.setdefault("art", {})["odooRef"] = vals["default_code"] or ""
                    hechos.append(f"artículo nuevo {vals['name']}")
                desc = art.get("nombre") or ""
                if art.get("subtitulo"):
                    desc += "\n" + art["subtitulo"]
                lineas.append({"product_id": prod, "product_uom_qty": qty, "price_unit": l.get("precio") or 0, "name": desc})
            else:
                if not generico:
                    r = odoo.search_read("product.product", [("default_code", "=", "PRESU-VARIOS")], ["id"], company_ids=[cid], limit=1)
                    if r:
                        generico = r[0]["id"]
                    else:
                        t = odoo.call("product.template", "create", [cid], vals_list=[{"name": "Concepto de presupuesto", "default_code": "PRESU-VARIOS",
                                                                                       "type": "service", "sale_ok": True, "purchase_ok": False, "list_price": 0}])
                        t = t[0] if isinstance(t, list) else t
                        generico = odoo.search_read("product.product", [("product_tmpl_id", "=", t)], ["id"], company_ids=[cid], limit=1)[0]["id"]
                lineas.append({"product_id": generico, "product_uom_qty": qty, "price_unit": l.get("precio") or 0,
                               "name": (l.get("descripcion") or "") + ("\n" + l["detalle"] if l.get("detalle") else "")})
        if not lineas:
            return "El presupuesto no tiene líneas"
        so = odoo.call("sale.order", "create", [cid], vals_list=[{
            "partner_id": pid, "company_id": cid, "client_order_ref": q.get("numero") or False,
            "note": q.get("notas") or False, "order_line": [[0, 0, x] for x in lineas]}])
        so = so[0] if isinstance(so, list) else so
        if confirmar:
            odoo.call("sale.order", "action_confirm", [cid], ids=[so])
        nm = odoo.search_read("sale.order", [("id", "=", so)], ["name", "amount_total"], company_ids=[cid])
        nombre = nm[0]["name"] if nm else str(so)
        q["odoo"] = {"id": so, "nombre": nombre, "empresa": comp["name"], "confirmado": confirmar}
        q["estado"] = "aceptado" if confirmar else q.get("estado", "borrador")
        return {"ok": True, "pedido": nombre, "total": nm[0]["amount_total"] if nm else None, "confirmado": confirmar,
                "empresa": comp["name"], "dado_de_alta": hechos, "enlace": f"{os.environ.get('ODOO_URL', '')}/odoo/sales/{so}"}

    def resumen(q):
        c = q.get("cliente") or {}
        ls = [{"id": l.get("id"), "tipo": l.get("tipo"), "nombre": (l.get("art") or {}).get("nombre") or l.get("descripcion"),
               "cantidad": l.get("cantidad"), "precio": l.get("precio"), "auto": l.get("precioAuto")} for l in q.get("lineas", [])]
        return json.dumps({"numero": q.get("numero"), "cliente": c, "lineas": ls, "notas": q.get("notas"), "sin_total": q.get("sinTotal"),
                           "validez_dias": q.get("validezDias"), "odoo": q.get("odoo")}, ensure_ascii=False)

    @app.post("/api/presupuestos/asistente")
    async def asistente(request: Request):
        current_user(request)
        if not clave():
            raise HTTPException(400, "Falta la clave de Claude: guarda el archivo claude_clave.txt en la carpeta datos del NAS")
        b = await request.json()
        q = b.get("quote") or {}
        hist = [m for m in (b.get("historial") or [])[-12:] if m.get("role") in ("user", "assistant") and m.get("content")]
        while hist and hist[0]["role"] != "user":
            hist.pop(0)
        sistema = ("Eres el asistente del presupuestador de Folder Castellón (papelería, regalo de empresa y merchandising personalizado). "
                   "Juan te escribe en español lo que quiere y tú montas el presupuesto con las herramientas: buscas el cliente en Odoo "
                   "(o lo pones a mano si no existe), buscas artículos en Odoo y en los guardados, y si te pide artículos de una web "
                   "la buscas o la abres con web_search/web_fetch, sacas nombre, descripción corta, referencia del proveedor, colores y las "
                   "URL de las fotos del producto (imágenes grandes, no logos ni miniaturas) y creas la ficha con anadir_ficha. "
                   "Los precios del presupuesto son de VENTA sin IVA: si de la web sacas un coste de proveedor y Juan no dice margen, "
                   "pregúntale o dilo claramente en tu respuesta; apunta el coste en coste_notas. No inventes precios ni datos. "
                   "Cuando Juan diga que el cliente lo acepta o que lo pases a Odoo: comprueba que tiene cliente y líneas, pregunta la empresa "
                   "(INGRAF para merchandising e imprenta, DISTRIPLUS para papelería) si no la ha dicho, y usa pasar_a_odoo; confirmar=true solo si dice "
                   "que lo pase como pedido confirmado. Después dile el número de Odoo y qué has dado de alta. "
                   "Contesta breve, diciendo qué has hecho y qué falta. Estado actual del presupuesto: " + resumen(q))
        msgs = hist + [{"role": "user", "content": b.get("mensaje") or ""}]
        hecho, n0 = [], json.dumps(q, sort_keys=True)

        def llamar():
            texto = ""
            for _ in range(14):
                r = web.post(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages", timeout=180, headers={
                    "x-api-key": clave(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": modelo, "max_tokens": 4000, "system": sistema, "tools": HERR, "messages": msgs})
                if r.status_code != 200:
                    raise HTTPException(502, f"Claude respondió {r.status_code}: {r.text[:300]}")
                res = r.json()
                bloques = res.get("content", [])
                texto = "".join(x.get("text", "") for x in bloques if x.get("type") == "text").strip() or texto
                usos = [x for x in bloques if x.get("type") == "tool_use"]
                msgs.append({"role": "assistant", "content": bloques})
                if res.get("stop_reason") == "pause_turn":
                    continue
                if not usos:
                    return texto
                resultados = []
                for u in usos:
                    try:
                        out = ejecutar(q, u["name"], u.get("input") or {})
                        if u["name"] not in ("buscar_clientes", "buscar_articulos", "guardar_imagen"):
                            hecho.append(u["name"])
                    except Exception as e:
                        out = f"Error: {e}"
                    resultados.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(out, ensure_ascii=False)[:20000]})
                msgs.append({"role": "user", "content": resultados})
            return texto or "He hecho varias cosas pero no he terminado; dime si sigo."

        import asyncio
        texto = await asyncio.to_thread(llamar)
        return {"texto": texto, "quote": q, "cambios": json.dumps(q, sort_keys=True) != n0, "acciones": hecho}
