"""Completar fichas de artículos desde la web del fabricante: foto y datos que falten (EAN, ref., descripción).
Claude busca la ficha oficial (con web_search / web_fetch y abriendo páginas desde el NAS), el panel descarga la foto
y enseña una propuesta; solo se escribe en Odoo lo que el usuario marque y nunca se pisa un dato que ya exista
(salvo la foto si se pide expresamente)."""
import base64
import json
import os
import re
import secrets
import threading
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import httpx
from fastapi import Request, HTTPException
from fastapi.responses import Response

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
      "Accept-Language": "es-ES,es;q=0.9,en;q=0.6"}


def setup(app, *, data_dir: Path, odoo, current_user, scope, guard):
    web = httpx.Client(timeout=40, follow_redirects=True, headers=UA)
    fotos_dir = data_dir / "fichas_fotos"
    fotos_dir.mkdir(parents=True, exist_ok=True)
    TRABAJOS = {}
    campos_tpl = {}

    def clave():
        f = data_dir / "claude_clave.txt"
        k = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", f.read_text(errors="ignore")) if f.exists() else []
        if not k:
            raise HTTPException(400, "Falta la clave de Claude en el NAS")
        return k[0]

    def tiene(campo, cids):
        if not campos_tpl:
            campos_tpl.update(odoo.call("product.template", "fields_get", cids, attributes=["type"]))
        return campo in campos_tpl

    def abrir_web(url, filtro=""):
        r = web.get(url)
        if r.status_code >= 400:
            return f"La página respondió {r.status_code}"
        html = r.text if "html" in (r.headers.get("content-type") or "") or r.text.lstrip().startswith("<") else ""
        if not html:
            return {"aviso": "No es una página HTML", "tipo": r.headers.get("content-type")}
        base = str(r.url)
        titulo = unescape((re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, ""])[1]).strip()
        metas = {}
        for m in re.finditer(r'<meta[^>]+(?:property|name)=["\'](og:[a-z:]+|description)["\'][^>]+content=["\']([^"\']*)', html, re.I):
            metas[m.group(1)] = unescape(m.group(2))
        imgs = []
        if metas.get("og:image"):
            imgs.append(urljoin(base, metas["og:image"]))
        for m in re.finditer(r'<img[^>]+?(?:data-zoom-image|data-large|data-src|src)=["\']([^"\']+)', html, re.I):
            u = urljoin(base, unescape(m.group(1)))
            if re.search(r"\.(jpe?g|png|webp)(\?|$)", u, re.I) and not re.search(r"logo|icon|sprite|banner|flag|pixel|placeholder", u, re.I) and u not in imgs:
                imgs.append(u)
        enlaces = []
        for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
            u = urljoin(base, unescape(m.group(1)))
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(m.group(2)))).strip()[:80]
            if u.startswith("http") and (not filtro or filtro.lower() in (u + " " + t).lower()) and (u, t) not in enlaces:
                enlaces.append((u, t))
        enlaces.sort(key=lambda e: 0 if re.search(r"\.html|/product|/producto|product_id=|/p/|/articulo|/item", e[0], re.I) else 1)
        texto = re.sub(r"<(script|style|noscript|svg)[\s\S]*?</\1>", " ", html, flags=re.I)
        texto = re.sub(r"<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", texto, flags=re.I)
        texto = re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", unescape(re.sub(r"<[^>]+>", " ", texto)))).strip()
        return {"url": base, "titulo": titulo, "meta": metas, "fotos": imgs[:25], "texto": texto[:9000],
                "enlaces": [{"url": u, "texto": t} for u, t in enlaces[:120]]}

    def bajar_foto(url):
        r = web.get(url, headers={"Referer": url.split("/", 3)[0] + "//" + url.split("/", 3)[2] + "/"})
        ct = (r.headers.get("content-type") or "").split(";")[0].strip()
        if r.status_code != 200 or not ct.startswith("image/") or len(r.content) < 4000 or len(r.content) > 8_000_000:
            raise ValueError(f"no vale ({r.status_code} {ct} {len(r.content)} bytes)")
        fid = secrets.token_hex(8)
        (fotos_dir / fid).write_bytes(r.content)
        (fotos_dir / (fid + ".t")).write_text(ct)
        return fid

    HERR = [
        {"name": "abrir_web", "description": "Abre una página web desde el servidor de Folder y devuelve título, texto, fotos y enlaces. Sirve para entrar en la web del fabricante o en su buscador (construye tú la URL de búsqueda).",
         "input_schema": {"type": "object", "properties": {"url": {"type": "string"}, "buscar_enlaces": {"type": "string"}}, "required": ["url"]}},
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 4},
        {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 5},
    ]

    def buscar_uno(p):
        datos = {k: p.get(k) for k in ("nombre", "ref_interna", "ean", "proveedor", "cod_proveedor", "descripcion_doc")}
        sistema = ("Eres el ayudante de compras de Folder Castellón (papelería). Te paso un artículo de Odoo y tienes que encontrar su ficha en la web del "
                   "FABRICANTE (edding.com, milan.es, stabilo.com, hp.com, brother.es, bic.com, pilot, faber-castell, etc.); si no hay ficha del fabricante, "
                   "una tienda o distribuidor serio. Usa web_search para localizarla y abrir_web o web_fetch para leerla. Comprueba que es exactamente el mismo "
                   "artículo (mismo modelo, color y formato; el EAN o la referencia del fabricante deben coincidir si los tienes). Elige la foto principal del "
                   "producto (preferible JPG o PNG grande, fondo blanco, sin logos ni banners). Cuando termines responde SOLO con un JSON: "
                   '{"url_ficha": "", "fotos": ["url foto principal", "otra"], "ean": "", "ref_fabricante": "", "marca": "", "nombre": "", '
                   '"descripcion": "descripción comercial corta en español (máx. 300 caracteres)", "seguro": true, "nota": "por qué es el mismo o qué dudas hay"}. '
                   "No inventes nada: si no encuentras un dato déjalo vacío; si no estás seguro de que sea el mismo artículo pon seguro=false.")
        msgs = [{"role": "user", "content": "Artículo: " + json.dumps(datos, ensure_ascii=False)}]
        texto = ""
        for _ in range(10):
            r = web.post(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages", timeout=180, headers={
                "x-api-key": clave(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": os.environ.get("PANEL_MODEL", "claude-sonnet-5-5"), "max_tokens": 3000, "system": sistema, "tools": HERR, "messages": msgs})
            if r.status_code != 200:
                raise RuntimeError(f"Claude respondió {r.status_code}: {r.text[:200]}")
            res = r.json()
            bloques = res.get("content", [])
            texto = "".join(x.get("text", "") for x in bloques if x.get("type") == "text").strip() or texto
            usos = [x for x in bloques if x.get("type") == "tool_use"]
            msgs.append({"role": "assistant", "content": bloques})
            if res.get("stop_reason") == "pause_turn":
                continue
            if not usos:
                break
            out = []
            for u in usos:
                try:
                    o = abrir_web(u["input"]["url"], u["input"].get("buscar_enlaces") or "")
                except Exception as e:
                    o = f"Error: {e}"
                out.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(o, ensure_ascii=False)[:15000]})
            msgs.append({"role": "user", "content": out})
        m = re.search(r"\{[\s\S]*\}", texto)
        if not m:
            return {"error": "No he encontrado la ficha", "nota": texto[:300]}
        try:
            w = json.loads(m.group(0))
        except Exception:
            return {"error": "Respuesta no válida", "nota": texto[:300]}
        w["foto_id"], w["foto_error"] = None, None
        for u in (w.get("fotos") or [])[:4]:
            try:
                w["foto_id"] = bajar_foto(u); w["foto_url"] = u
                break
            except Exception as e:
                w["foto_error"] = str(e)
        return w

    def investigar_linea(linea, proveedor, candidatos):
        """Línea de un albarán/factura de proveedor que no se ha identificado: Claude mira los candidatos de Odoo y la web del
        fabricante y dice si es uno de ellos o qué es (EAN, ref. fabricante, nombre, foto) para darlo de alta sin duplicar."""
        sistema = ("Eres el ayudante de compras de Folder Castellón (papelería). Te paso una línea de un albarán o factura de proveedor que no "
                   "hemos encontrado en Odoo y una lista de artículos de Odoo que se le parecen (candidatos). Tareas: "
                   "1) Averigua qué artículo es exactamente: busca en la web del FABRICANTE (o del propio proveedor/distribuidor) por la referencia y la "
                   "descripción; usa web_search y abrir_web/web_fetch. Saca su EAN-13, la referencia del fabricante, marca, nombre comercial y una foto. "
                   "2) Mira si alguno de los candidatos de Odoo es EXACTAMENTE ese artículo (mismo modelo, mismo color, mismo formato/unidades; si el "
                   "candidato tiene EAN y coincide con el que encuentres, es ese). Un color o tamaño distinto NO es el mismo. "
                   "Responde SOLO con un JSON: {\"odoo_id\": id del candidato o null, \"ean\": \"\", \"ref_fabricante\": \"\", \"marca\": \"\", "
                   "\"nombre\": \"nombre para la ficha: Marca Modelo Color/Formato\", \"descripcion\": \"máx. 300 caracteres\", \"fotos\": [\"url\"], "
                   "\"url_ficha\": \"\", \"seguro\": true, \"nota\": \"breve\"}. No inventes datos: lo que no encuentres, vacío; si dudas, seguro=false.")
        msgs = [{"role": "user", "content": "Proveedor: " + (proveedor or "") + "\nLínea del documento: " + json.dumps(linea, ensure_ascii=False)
                 + "\nCandidatos de Odoo: " + json.dumps(candidatos, ensure_ascii=False)}]
        texto = ""
        for _ in range(10):
            r = web.post(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages", timeout=180, headers={
                "x-api-key": clave(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": os.environ.get("PANEL_MODEL", "claude-sonnet-5-5"), "max_tokens": 3000, "system": sistema, "tools": HERR, "messages": msgs})
            if r.status_code != 200:
                raise RuntimeError(f"Claude respondió {r.status_code}: {r.text[:200]}")
            res = r.json()
            bloques = res.get("content", [])
            texto = "".join(x.get("text", "") for x in bloques if x.get("type") == "text").strip() or texto
            usos = [x for x in bloques if x.get("type") == "tool_use"]
            msgs.append({"role": "assistant", "content": bloques})
            if res.get("stop_reason") == "pause_turn":
                continue
            if not usos:
                break
            out = []
            for u in usos:
                try:
                    o = abrir_web(u["input"]["url"], u["input"].get("buscar_enlaces") or "")
                except Exception as e:
                    o = f"Error: {e}"
                out.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(o, ensure_ascii=False)[:15000]})
            msgs.append({"role": "user", "content": out})
        m = re.search(r"\{[\s\S]*\}", texto)
        if not m:
            return {"error": "sin respuesta", "nota": texto[:200]}
        try:
            w = json.loads(m.group(0))
        except Exception:
            return {"error": "respuesta no válida", "nota": texto[:200]}
        w["foto_id"] = None
        for u in (w.get("fotos") or [])[:4]:
            try:
                w["foto_id"] = bajar_foto(u)
                break
            except Exception:
                pass
        return w

    app.state.investigar_linea = investigar_linea
    app.state.fichas_fotos = fotos_dir

    def leer_productos(pids, cids):
        f = ["display_name", "name", "default_code", "barcode", "image_128", "product_tmpl_id", "description_sale", "seller_ids"]
        extra = [c for c in ("x_studio_proveedor", "available_in_pos") if tiene(c, cids)]
        ps = odoo.search_read("product.product", [("id", "in", pids)], f + extra, company_ids=cids)
        sids = [s for p in ps for s in p.get("seller_ids") or []]
        sel = {s["id"]: s for s in odoo.search_read("product.supplierinfo", [("id", "in", sids)], ["partner_id", "product_code"], company_ids=cids)} if sids else {}
        out = []
        for p in ps:
            s = next((sel[i] for i in p.get("seller_ids") or [] if i in sel), None)
            out.append({"product_id": p["id"], "tmpl_id": p["product_tmpl_id"][0], "nombre": p["display_name"], "ref_interna": p.get("default_code") or "",
                        "ean": p.get("barcode") or "", "tiene_foto": bool(p.get("image_128")), "descripcion": p.get("description_sale") or "",
                        "proveedor": (s or {}).get("partner_id", [0, ""])[1] if s else "", "cod_proveedor": (s or {}).get("product_code") or "",
                        "x_proveedor": p.get("x_studio_proveedor") if "x_studio_proveedor" in extra else None,
                        "en_tpv": p.get("available_in_pos") if "available_in_pos" in extra else None})
        return out

    @app.post("/api/fichas/de_pedido")
    async def de_pedido(request: Request):
        """Artículos de un pedido de compra (por id o nombre) con lo que les falta."""
        u = current_user(request)
        cids, _ = scope(u, "all")
        b = await request.json()

        def run():
            dom = [("id", "=", int(b["pedido_id"]))] if b.get("pedido_id") else [("name", "=", b.get("pedido") or "")]
            po = odoo.search_read("purchase.order", dom, ["name", "partner_id"], company_ids=cids, limit=1)
            if not po:
                raise HTTPException(404, "Pedido no encontrado")
            pl = odoo.search_read("purchase.order.line", [("order_id", "=", po[0]["id"]), ("display_type", "=", False)], ["product_id", "name"], company_ids=cids)
            pids = list(dict.fromkeys(l["product_id"][0] for l in pl if l.get("product_id")))
            ps = leer_productos(pids, cids)
            desc = {l["product_id"][0]: l["name"] for l in pl if l.get("product_id")}
            for p in ps:
                p["descripcion_doc"] = desc.get(p["product_id"], "")
                if not p["proveedor"]:
                    p["proveedor"] = po[0]["partner_id"][1]
            return {"pedido": po[0]["name"], "proveedor": po[0]["partner_id"][1], "productos": ps}
        return guard(run)

    @app.post("/api/fichas/buscar")
    async def buscar(request: Request):
        """Lanza la búsqueda en segundo plano (tarda ~30-60 s por artículo). Devuelve el id del trabajo."""
        u = current_user(request)
        cids, _ = scope(u, "all")
        b = await request.json()
        prods = b.get("productos") or []
        if not prods:
            raise HTTPException(400, "No hay artículos")
        clave()
        tid = secrets.token_hex(6)
        TRABAJOS[tid] = {"total": len(prods), "hechos": 0, "res": {}, "fin": False, "t": time.time()}

        def trabajo():
            def uno(p):
                try:
                    TRABAJOS[tid]["res"][str(p["product_id"])] = buscar_uno(p)
                except Exception as e:
                    TRABAJOS[tid]["res"][str(p["product_id"])] = {"error": str(e)[:300]}
                TRABAJOS[tid]["hechos"] += 1
            hilos = []
            for p in prods:  # de 3 en 3
                h = threading.Thread(target=uno, args=(p,), daemon=True); h.start(); hilos.append(h)
                if len([x for x in hilos if x.is_alive()]) >= 3:
                    for x in hilos:
                        x.join()
            for x in hilos:
                x.join()
            TRABAJOS[tid]["fin"] = True
        threading.Thread(target=trabajo, daemon=True).start()
        for k in [k for k, v in TRABAJOS.items() if time.time() - v["t"] > 6 * 3600]:
            TRABAJOS.pop(k, None)
        return {"trabajo": tid}

    @app.get("/api/fichas/trabajo/{tid}")
    def estado(tid: str, request: Request):
        current_user(request)
        t = TRABAJOS.get(tid)
        if not t:
            raise HTTPException(404, "Trabajo no encontrado (¿se reinició el panel?)")
        return t

    @app.get("/api/fichas/foto/{fid}")
    def foto(fid: str, request: Request):
        current_user(request)
        if not re.fullmatch(r"[0-9a-f]{16}", fid) or not (fotos_dir / fid).exists():
            raise HTTPException(404)
        return Response((fotos_dir / fid).read_bytes(), media_type=(fotos_dir / (fid + ".t")).read_text())

    @app.get("/api/fichas/foto_odoo/{pid}")
    def foto_odoo(pid: int, request: Request):
        u = current_user(request)
        cids, _ = scope(u, "all")
        p = odoo.search_read("product.product", [("id", "=", pid)], ["image_128"], company_ids=cids)
        if not p or not p[0].get("image_128"):
            raise HTTPException(404)
        return Response(base64.b64decode(p[0]["image_128"]), media_type="image/png")

    @app.post("/api/fichas/aplicar")
    async def aplicar(request: Request):
        """items: [{product_id, foto_id?, pisar_foto?, ean?, ref?, descripcion?, proveedor?, tpv?}] — solo rellena lo vacío."""
        u = current_user(request)
        cids, _ = scope(u, "all")
        b = await request.json()

        def run():
            hechos, avisos = [], []
            actuales = {p["product_id"]: p for p in leer_productos([int(i["product_id"]) for i in b.get("items") or []], cids)}
            for it in b.get("items") or []:
                pid = int(it["product_id"])
                p = actuales.get(pid)
                if not p:
                    continue
                vt, vp, que = {}, {}, []
                if it.get("foto_id") and re.fullmatch(r"[0-9a-f]{16}", it["foto_id"]) and (fotos_dir / it["foto_id"]).exists() and (not p["tiene_foto"] or it.get("pisar_foto")):
                    vt["image_1920"] = base64.b64encode((fotos_dir / it["foto_id"]).read_bytes()).decode(); que.append("foto")
                ean = re.sub(r"\D", "", str(it.get("ean") or ""))
                if ean and not p["ean"]:
                    otro = odoo.search_read("product.product", [("barcode", "=", ean), ("id", "!=", pid)], ["display_name"], company_ids=cids, limit=1)
                    if otro:
                        avisos.append(f"{p['nombre']}: el EAN {ean} ya lo tiene {otro[0]['display_name']} (¿duplicado?)")
                    else:
                        vp["barcode"] = ean; que.append("EAN")
                if it.get("ref") and not p["ref_interna"]:
                    vp["default_code"] = it["ref"]; que.append("ref.")
                if it.get("descripcion") and not p["descripcion"]:
                    vt["description_sale"] = it["descripcion"][:500]; que.append("descripción")
                if it.get("proveedor") and p["x_proveedor"] is not None and not p["x_proveedor"]:
                    vt["x_studio_proveedor"] = it["proveedor"]; que.append("proveedor")
                if it.get("tpv") and p["en_tpv"] is False:
                    vt["available_in_pos"] = True; que.append("TPV")
                try:
                    if vt:
                        odoo.call("product.template", "write", cids, ids=[p["tmpl_id"]], vals=vt)
                    if vp:
                        odoo.call("product.product", "write", cids, ids=[pid], vals=vp)
                    if que:
                        hechos.append(f"{p['nombre']}: {', '.join(que)}")
                except Exception as e:
                    avisos.append(f"{p['nombre']}: {str(e)[:150]}")
            return {"hechos": hechos, "avisos": avisos}
        return guard(run)
