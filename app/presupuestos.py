"""Presupuestador: almacén de documentos (presupuestos, artículos, ajustes), fotos,
y clientes/artículos de Odoo en directo para el buscador."""
import base64
import json
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
                ["name", "display_name", "vat", "street", "zip", "city", "email", "phone", "mobile",
                 "parent_id", "type", "is_company"],
                all_company_ids(), limit=20000)
            out = []
            for r in rows:
                parent = r["parent_id"][1] if r.get("parent_id") else ""
                person = not r.get("is_company") and parent
                out.append({
                    "i": r["id"],
                    "n": parent if person else r.get("name") or r.get("display_name") or "",
                    "c": r.get("name") if person else "",
                    "v": r.get("vat") or "",
                    "m": r.get("email") or "",
                    "d": r.get("street") or "",
                    "p": " ".join(x for x in [r.get("zip") or "", r.get("city") or ""] if x),
                    "f": r.get("phone") or r.get("mobile") or "",
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
