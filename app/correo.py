"""Correo integrado: n8n (que ya lee las cuentas del NAS) manda cada email al panel; Claude lo clasifica.
- Facturas / albaranes de proveedor en PDF -> entran solos en la Bandeja de Compras.
- Pedidos de cliente -> "Preparar pedido": identifica al cliente, interpreta lo que pide con su histórico
  ("el tóner de siempre") y propone opciones por línea; se crea el pedido en borrador en DISTRIPLUS.
Las claves de correo siguen solo en n8n; el panel no las ve.
"""
import asyncio
import base64
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

import httpx
from fastapi import Request, HTTPException
from fastapi.responses import Response

DISTRI = 1
GENERICOS = {"gmail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es", "yahoo.es", "yahoo.com", "icloud.com", "live.com", "msn.com", "telefonica.net", "gva.es"}
CLASES = ["pedido", "presupuesto", "factura_proveedor", "albaran_proveedor", "consulta", "reclamacion", "publicidad", "otro"]


def setup(app, *, data_dir: Path, odoo, current_user, scope, guard):
    db = data_dir / "correo.db"
    carpeta = data_dir / "correo"
    carpeta.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    http = httpx.Client(timeout=180)
    modelo = os.environ.get("PANEL_MODEL", "claude-sonnet-5-5")

    def con():
        c = sqlite3.connect(db, timeout=20)
        c.row_factory = sqlite3.Row
        c.executescript("""
        CREATE TABLE IF NOT EXISTS mensajes (id TEXT PRIMARY KEY, msgid TEXT, cuenta TEXT, de TEXT, nombre TEXT, asunto TEXT, fecha TEXT, texto TEXT,
          adjuntos TEXT, clase TEXT, urgencia TEXT, resumen TEXT, que_hacer TEXT, estado TEXT, datos TEXT, partner_id INTEGER, pedido TEXT, recibido REAL, usuario TEXT);
        CREATE INDEX IF NOT EXISTS ix_msgid ON mensajes(msgid);
        """)
        return c

    def clave():
        f = data_dir / "claude_clave.txt"
        k = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", f.read_text(errors="ignore")) if f.exists() else []
        if not k:
            raise HTTPException(400, "Falta la clave de Claude en el NAS")
        return k[0]

    def token_ok(request):
        t = request.headers.get("x-panel-token") or ""
        try:
            w = sqlite3.connect(data_dir / "whatsapp.db")
            r = w.execute("SELECT v FROM ajustes WHERE k='llamadas_token'").fetchone()
            w.close()
            return bool(t and r and secrets.compare_digest(t, r[0]))
        except Exception:
            return False

    def claude(system, msgs, tools=None, max_tokens=3000):
        body = {"model": modelo, "max_tokens": max_tokens, "system": system, "messages": msgs}
        if tools:
            body["tools"] = tools
        r = http.post(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages",
                      headers={"x-api-key": clave(), "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"Claude respondió {r.status_code}: {r.text[:200]}")
        return r.json()

    def json_de(texto):
        m = re.search(r"\{[\s\S]*\}", texto or "")
        return json.loads(m.group(0)) if m else {}

    # ---------- clasificar ----------
    def clasificar(mid):
        with con() as c:
            m = c.execute("SELECT * FROM mensajes WHERE id=?", (mid,)).fetchone()
        if not m:
            return
        adj = json.loads(m["adjuntos"] or "[]")
        try:
            res = claude("Clasificas correos que llegan a Folder Castellón (papelería, material de oficina, imprenta). Responde SOLO un JSON.",
                         [{"role": "user", "content": f"De: {m['nombre']} <{m['de']}>\nCuenta: {m['cuenta']}\nAsunto: {m['asunto']}\nAdjuntos: {', '.join(a['nombre'] for a in adj) or 'ninguno'}\n\n"
                           f"{(m['texto'] or '')[:6000]}\n\n"
                           '---\nDevuelve {"clase": "' + "|".join(CLASES) + '", "urgencia": "alta|media|baja", "resumen": "qué pasa en una frase", '
                           '"que_hacer": "qué hay que hacer en una frase", "plazo": "si da una fecha o urgencia, cuál"}. '
                           "pedido = un cliente nos pide material; presupuesto = nos pide precio; factura_proveedor / albaran_proveedor = un proveedor nos manda su factura o albarán."}],
                         max_tokens=600)
            d = json_de("".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text"))
        except Exception as e:
            d = {"clase": "otro", "urgencia": "media", "resumen": f"No se pudo clasificar: {e}"[:200], "que_hacer": ""}
        clase = d.get("clase") if d.get("clase") in CLASES else "otro"
        nota = d.get("que_hacer") or ""
        # facturas / albaranes de proveedor en PDF -> Bandeja de Compras
        if clase in ("factura_proveedor", "albaran_proveedor") and getattr(app.state, "compras_alta", None):
            pasados = 0
            for a in adj:
                if a.get("doc_id") or not a["nombre"].lower().endswith(".pdf"):
                    continue
                try:
                    a["doc_id"] = app.state.compras_alta(a["nombre"], (carpeta / a["fichero"]).read_bytes(),
                                                         "factura" if clase == "factura_proveedor" else "albaran", DISTRI, "correo")
                    pasados += 1
                except Exception:
                    pass
            if pasados:
                nota = f"{pasados} PDF pasado(s) a la Bandeja de Compras"
        with lock, con() as c:
            c.execute("UPDATE mensajes SET clase=?, urgencia=?, resumen=?, que_hacer=?, adjuntos=?, estado=? WHERE id=?",
                      (clase, d.get("urgencia") or "media", (d.get("resumen") or "")[:400], (nota + (f" · Plazo: {d['plazo']}" if d.get("plazo") else ""))[:400],
                       json.dumps(adj, ensure_ascii=False), "hecho" if clase == "publicidad" else "nuevo", mid))

    @app.post("/api/correo/entrada")
    async def entrada(request: Request):
        """Lo llama n8n por cada correo nuevo (cabecera X-Panel-Token). Acepta JSON o multipart con adjuntos."""
        if not token_ok(request):
            current_user(request)
        ct = request.headers.get("content-type", "")
        adjuntos = []
        if "multipart" in ct:
            form = await request.form()
            campos = {k: v for k, v in form.items() if isinstance(v, str)}
            for k, v in form.multi_items():
                if hasattr(v, "read"):
                    adjuntos.append((v.filename or k, v.content_type or "", await v.read()))
        else:
            campos = await request.json()
            for a in campos.get("adjuntos") or []:
                try:
                    adjuntos.append((a.get("nombre") or "adjunto", a.get("mime") or "", base64.b64decode(a.get("base64") or a.get("data") or "")))
                except Exception:
                    pass
        return guardar(campos, adjuntos)

    def guardar(campos, adjuntos):
        msgid = (campos.get("message_id") or campos.get("messageId") or "").strip()
        if msgid:
            with con() as c:
                if c.execute("SELECT 1 FROM mensajes WHERE msgid=?", (msgid,)).fetchone():
                    return {"ok": True, "repetido": True}
        de = campos.get("de") or campos.get("from") or ""
        mm = re.search(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", de)
        email = mm.group(0).lower() if mm else de
        nombre = re.sub(r"<.*?>", "", de).replace('"', "").strip() or email
        texto = campos.get("texto") or campos.get("text") or ""
        if not texto and campos.get("html"):
            texto = re.sub(r"\n\s*\n+", "\n", re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[\s\S]*?</\1>", " ", campos["html"])))
        mid = secrets.token_hex(8)
        adj = []
        for n, mime, datos in adjuntos:
            if not datos or len(datos) > 25_000_000:
                continue
            f = f"{mid}_{len(adj)}" + (Path(n).suffix[:8] or "")
            (carpeta / f).write_bytes(datos)
            adj.append({"nombre": n, "mime": mime, "fichero": f, "bytes": len(datos)})
        with lock, con() as c:
            c.execute("INSERT INTO mensajes (id, msgid, cuenta, de, nombre, asunto, fecha, texto, adjuntos, estado, recibido) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (mid, msgid, campos.get("cuenta") or campos.get("to") or "", email, nombre, campos.get("asunto") or campos.get("subject") or "",
                       campos.get("fecha") or campos.get("date") or "", texto[:60000], json.dumps(adj, ensure_ascii=False), "clasificando", time.time()))
        threading.Thread(target=clasificar, args=(mid,), daemon=True).start()
        return {"ok": True, "id": mid}

    # ---------- lectura directa de las cuentas (IMAP), sin n8n ----------
    cuentas_f = data_dir / "correo_cuentas.txt"
    if not cuentas_f.exists():
        cuentas_f.write_text(
            "# Cuentas de correo que lee el Panel Folder (una por línea). Formato:\n"
            "# correo;contraseña;servidor IMAP\n"
            "# Ejemplos de servidor: imap.ionos.es (IONOS) · imap.nominalia.com (Nominalia)\n"
            "# Quita la # del principio de cada línea que rellenes. El panel no marca los correos como leídos.\n"
            "#info@foldercastellon.es;CONTRASEÑA;imap.ionos.es\n", encoding="utf-8")
    ESTADO_IMAP = {}

    def leer_cuentas():
        out = []
        for l in cuentas_f.read_text(encoding="utf-8", errors="ignore").splitlines():
            l = l.strip()
            if not l or l.startswith("#"):
                continue
            p = [x.strip() for x in l.split(";")]
            if len(p) >= 3:
                out.append({"usuario": p[0], "clave": p[1], "servidor": p[2], "puerto": int(p[3]) if len(p) > 3 and p[3].isdigit() else 993})
        return out

    def dec(v):
        from email.header import decode_header, make_header
        try:
            return str(make_header(decode_header(v or "")))
        except Exception:
            return v or ""

    def revisar_cuenta(cta):
        import imaplib
        import email
        from email.utils import parsedate_to_datetime
        with con() as c:
            c.execute("CREATE TABLE IF NOT EXISTS imap_estado (cuenta TEXT PRIMARY KEY, uidvalidity TEXT, ultimo INTEGER)")
            st = c.execute("SELECT * FROM imap_estado WHERE cuenta=?", (cta["usuario"],)).fetchone()
        M = imaplib.IMAP4_SSL(cta["servidor"], cta["puerto"], timeout=60)
        try:
            M.login(cta["usuario"], cta["clave"])
            typ, d = M.select("INBOX", readonly=True)
            uv = ""
            try:
                uv = M.response("UIDVALIDITY")[1][0].decode()
            except Exception:
                pass
            if st and st["uidvalidity"] == uv:
                typ, d = M.uid("search", None, f"UID {int(st['ultimo']) + 1}:*")
            else:  # primera vez: solo los dos últimos días
                desde = time.strftime("%d-%b-%Y", time.gmtime(time.time() - 2 * 86400))
                typ, d = M.uid("search", None, f"SINCE {desde}")
            uids = [int(x) for x in (d[0] or b"").split()]
            if st and st["uidvalidity"] == uv:
                uids = [u for u in uids if u > int(st["ultimo"])]
            nuevos = 0
            for uid in sorted(uids)[:200]:
                typ, data = M.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((x[1] for x in data if isinstance(x, tuple)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                texto, html, adj = "", "", []
                for part in msg.walk():
                    if part.is_multipart():
                        continue
                    fn = part.get_filename()
                    ct = part.get_content_type()
                    payload = part.get_payload(decode=True) or b""
                    if fn or part.get_content_disposition() == "attachment":
                        adj.append((dec(fn) or "adjunto", ct, payload))
                    elif ct == "text/plain" and not texto:
                        texto = payload.decode(part.get_content_charset() or "utf-8", "replace")
                    elif ct == "text/html" and not html:
                        html = payload.decode(part.get_content_charset() or "utf-8", "replace")
                try:
                    fecha = parsedate_to_datetime(msg.get("Date")).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    fecha = msg.get("Date") or ""
                r = guardar({"de": dec(msg.get("From")), "cuenta": cta["usuario"], "asunto": dec(msg.get("Subject")), "fecha": fecha,
                             "texto": texto, "html": html, "message_id": (msg.get("Message-ID") or "").strip()}, adj)
                nuevos += 0 if r.get("repetido") else 1
                with lock, con() as c:
                    c.execute("INSERT OR REPLACE INTO imap_estado VALUES (?,?,?)", (cta["usuario"], uv, uid))
            if not uids:
                with lock, con() as c:
                    if not st or st["uidvalidity"] != uv:
                        typ, d = M.uid("search", None, "ALL")
                        todos = [int(x) for x in (d[0] or b"").split()]
                        c.execute("INSERT OR REPLACE INTO imap_estado VALUES (?,?,?)", (cta["usuario"], uv, max(todos) if todos else 0))
            return nuevos
        finally:
            try:
                M.logout()
            except Exception:
                pass

    def bucle_imap():
        while True:
            try:
                for cta in leer_cuentas():
                    try:
                        n = revisar_cuenta(cta)
                        ESTADO_IMAP[cta["usuario"]] = {"ok": True, "ultima": time.time(), "nuevos": n}
                    except Exception as e:
                        ESTADO_IMAP[cta["usuario"]] = {"ok": False, "ultima": time.time(), "error": str(e)[:200]}
            except Exception:
                pass
            time.sleep(120)
    threading.Thread(target=bucle_imap, daemon=True).start()

    @app.get("/api/correo/cuentas")
    def cuentas(request: Request):
        current_user(request)
        cs = leer_cuentas()
        return [{"cuenta": c["usuario"], "servidor": c["servidor"], **ESTADO_IMAP.get(c["usuario"], {"ok": None})} for c in cs]

    @app.get("/api/correo")
    def lista(request: Request, estado: str = "pendientes"):
        current_user(request)
        q = "SELECT id, cuenta, de, nombre, asunto, fecha, clase, urgencia, resumen, que_hacer, estado, pedido, recibido, adjuntos FROM mensajes"
        q += " WHERE estado IN ('nuevo','clasificando')" if estado == "pendientes" else " WHERE estado NOT IN ('nuevo','clasificando')"
        with con() as c:
            rs = [dict(r) for r in c.execute(q + " ORDER BY recibido DESC LIMIT 300").fetchall()]
        orden = {"alta": 0, "media": 1, "baja": 2}
        for r in rs:
            r["adjuntos"] = [{"nombre": a["nombre"], "i": i, "doc": bool(a.get("doc_id"))} for i, a in enumerate(json.loads(r["adjuntos"] or "[]"))]
        if estado == "pendientes":
            rs.sort(key=lambda r: (orden.get(r["urgencia"], 1), -r["recibido"]))
        return rs

    @app.get("/api/correo/{mid}")
    def ver(mid: str, request: Request):
        current_user(request)
        with con() as c:
            r = c.execute("SELECT * FROM mensajes WHERE id=?", (mid,)).fetchone()
        if not r:
            raise HTTPException(404)
        d = dict(r)
        d["adjuntos"] = [{"nombre": a["nombre"], "i": i, "doc": bool(a.get("doc_id"))} for i, a in enumerate(json.loads(d["adjuntos"] or "[]"))]
        d["datos"] = json.loads(d["datos"]) if d.get("datos") else None
        return d

    @app.get("/api/correo/{mid}/adjunto/{i}")
    def adjunto(mid: str, i: int, request: Request):
        current_user(request)
        with con() as c:
            r = c.execute("SELECT adjuntos FROM mensajes WHERE id=?", (mid,)).fetchone()
        a = json.loads(r["adjuntos"])[i] if r else None
        if not a:
            raise HTTPException(404)
        return Response((carpeta / a["fichero"]).read_bytes(), media_type=a.get("mime") or "application/octet-stream",
                        headers={"Content-Disposition": f'inline; filename="{a["nombre"]}"'})

    @app.post("/api/correo/{mid}/estado")
    async def estado(mid: str, request: Request):
        u = current_user(request)
        b = await request.json()
        e = b.get("estado")
        if e not in ("nuevo", "hecho", "descartado"):
            raise HTTPException(400)
        with lock, con() as c:
            c.execute("UPDATE mensajes SET estado=?, usuario=? WHERE id=?", (e, u, mid))
        return {"ok": True}

    # ---------- preparar pedido ----------
    def buscar_cliente(email, cids):
        ps = odoo.search_read("res.partner", [("email", "=ilike", email)], ["display_name", "commercial_partner_id", "email"], company_ids=cids, limit=5)
        if not ps and "@" in email and email.split("@")[1] not in GENERICOS:
            ps = odoo.search_read("res.partner", [("email", "ilike", "@" + email.split("@")[1])], ["display_name", "commercial_partner_id", "email"], company_ids=cids, limit=8)
        vistos, out = set(), []
        for p in ps:
            cp = p["commercial_partner_id"]
            if cp[0] not in vistos:
                vistos.add(cp[0]); out.append({"id": cp[0], "nombre": cp[1], "contacto_id": p["id"], "contacto": p["display_name"]})
        return out

    def historial(pid, cids):
        ls = odoo.search_read("sale.order.line", [("order_id.partner_id", "child_of", pid), ("order_id.state", "in", ["sale", "done"]), ("display_type", "=", False)],
                              ["product_id", "product_uom_qty", "price_unit", "discount", "order_id", "create_date"], company_ids=cids, limit=3000, order="id desc")
        g = {}
        for l in ls:
            if not l.get("product_id"):
                continue
            e = g.setdefault(l["product_id"][0], {"product_id": l["product_id"][0], "articulo": l["product_id"][1], "veces": 0, "cantidad": 0,
                                                  "ultimo_precio": round(l["price_unit"] * (1 - (l.get("discount") or 0) / 100), 4),
                                                  "ultima_fecha": (l.get("create_date") or "")[:10], "cant_habitual": l["product_uom_qty"]})
            e["veces"] += 1
            e["cantidad"] += l["product_uom_qty"]
        return sorted(g.values(), key=lambda e: (-e["veces"], e["articulo"]))

    def stock_ac(pids, cids):
        if not pids:
            return {}
        qs = odoo.search_read("stock.quant", [("product_id", "in", pids), ("location_id.usage", "=", "internal"), ("location_id.complete_name", "ilike", "AC/")],
                              ["product_id", "quantity", "reserved_quantity"], company_ids=cids, limit=2000)
        out = {}
        for q in qs:
            out[q["product_id"][0]] = out.get(q["product_id"][0], 0) + q["quantity"] - q["reserved_quantity"]
        return out

    HERR = [{"name": "buscar_articulos", "description": "Busca en el catálogo por nombre, referencia o EAN (también por palabras sueltas: 'toner hp 305 negro').",
             "input_schema": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]}}]

    def buscar_articulos(t, cids):
        t = t.strip()
        ps = odoo.search_read("product.product", ["|", "|", ("name", "ilike", t), ("default_code", "ilike", t), ("barcode", "=", t), ("sale_ok", "=", True)],
                              ["display_name", "lst_price"], company_ids=cids, limit=12)
        if not ps:
            pal = [w for w in re.split(r"\s+", t) if len(w) > 1]
            if len(pal) > 1:
                ps = odoo.search_read("product.product", [("name", "ilike", w) for w in pal] + [("sale_ok", "=", True)], ["display_name", "lst_price"], company_ids=cids, limit=12)
        return [{"product_id": p["id"], "articulo": p["display_name"], "pvp": p["lst_price"]} for p in ps]

    @app.post("/api/correo/{mid}/preparar")
    async def preparar(mid: str, request: Request):
        u = current_user(request)
        cids, _ = scope(u, "all")
        b = await request.json()
        with con() as c:
            m = c.execute("SELECT * FROM mensajes WHERE id=?", (mid,)).fetchone()
        if not m:
            raise HTTPException(404)

        def run():
            clientes = buscar_cliente(m["de"], cids)
            pid = int(b.get("partner_id") or 0) or (clientes[0]["id"] if clientes else 0)
            if not pid:
                return {"clientes": [], "lineas": [], "aviso": "No encuentro el cliente por su email: búscalo y elígelo."}
            nombre_cli = next((x["nombre"] for x in clientes if x["id"] == pid), None) or odoo.search_read("res.partner", [("id", "=", pid)], ["display_name"], company_ids=cids)[0]["display_name"]
            hist = historial(pid, cids)
            sistema = ("Eres quien prepara los pedidos de Folder Castellón (papelería, material de oficina, tóner). Te paso el correo de un cliente y lo que ese cliente "
                       "nos ha comprado antes. Interpreta qué pide, línea a línea. Muchas veces dicen cosas como 'el tóner de siempre' o 'lo mismo que la otra vez': "
                       "usa el histórico para deducirlo. Para cada línea da 1-4 opciones de artículos de Odoo (product_id reales: del histórico o buscados con "
                       "buscar_articulos), la más probable primero, con el motivo ('lo compra siempre', 'lo pidió en marzo', 'mismo modelo en otro color', 'parecido'). "
                       "Si no dice cantidad, usa la que suele pedir. No inventes product_id. Responde al final SOLO un JSON: "
                       '{"lineas": [{"pide": "texto del cliente", "cantidad": 1, "opciones": [{"product_id": 0, "articulo": "", "motivo": ""}], "duda": "si hay que confirmar algo"}], '
                       '"nota_cliente": "instrucciones de entrega, plazo, etc.", "pregunta_al_cliente": "si falta información, qué preguntarle"}')
            msgs = [{"role": "user", "content": f"Cliente: {nombre_cli}\nAsunto: {m['asunto']}\nCorreo:\n{(m['texto'] or '')[:8000]}\n\n"
                     f"Histórico de compras (más pedido primero): {json.dumps(hist[:120], ensure_ascii=False)}"}]
            texto = ""
            for _ in range(8):
                res = claude(sistema, msgs, HERR, 4000)
                bl = res.get("content", [])
                texto = "".join(x.get("text", "") for x in bl if x.get("type") == "text") or texto
                usos = [x for x in bl if x.get("type") == "tool_use"]
                msgs.append({"role": "assistant", "content": bl})
                if not usos:
                    break
                msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": t["id"],
                                                          "content": json.dumps(buscar_articulos(t["input"].get("texto", ""), cids), ensure_ascii=False)} for t in usos]})
            d = json_de(texto)
            hmap = {h["product_id"]: h for h in hist}
            pids = list({o["product_id"] for l in d.get("lineas") or [] for o in l.get("opciones") or [] if o.get("product_id")})
            validos = {p["id"]: p for p in odoo.search_read("product.product", [("id", "in", pids)], ["display_name", "lst_price"], company_ids=cids)} if pids else {}
            st = stock_ac(list(validos), cids)
            for l in d.get("lineas") or []:
                ops = []
                for o in l.get("opciones") or []:
                    p = validos.get(o.get("product_id"))
                    if not p:
                        continue
                    h = hmap.get(p["id"])
                    ops.append({"product_id": p["id"], "articulo": p["display_name"], "motivo": o.get("motivo") or "", "pvp": p["lst_price"],
                                "ultimo_precio": h["ultimo_precio"] if h else None, "ultima_fecha": h["ultima_fecha"] if h else None,
                                "veces": h["veces"] if h else 0, "stock_ac": round(st.get(p["id"], 0), 2)})
                l["opciones"] = ops
                l["elegido"] = ops[0]["product_id"] if ops else None
            d.update(clientes=clientes, partner_id=pid, cliente=nombre_cli, historial=hist[:40])
            with lock, con() as c:
                c.execute("UPDATE mensajes SET datos=?, partner_id=? WHERE id=?", (json.dumps(d, ensure_ascii=False), pid, mid))
            return d
        return await asyncio.to_thread(lambda: guard(run))

    @app.get("/api/correo/clientes/buscar")
    def clientes_buscar(request: Request, q: str = ""):
        u = current_user(request)
        cids, _ = scope(u, "all")
        return guard(lambda: [{"id": p["id"], "nombre": p["display_name"]} for p in odoo.search_read(
            "res.partner", ["|", ("name", "ilike", q), ("vat", "ilike", q), ("parent_id", "=", False)], ["display_name"], company_ids=cids, limit=15)])

    @app.get("/api/correo/articulos/buscar")
    def articulos_buscar(request: Request, q: str = ""):
        u = current_user(request)
        cids, _ = scope(u, "all")
        return guard(lambda: buscar_articulos(q, cids))

    @app.post("/api/correo/{mid}/pedido")
    async def crear_pedido(mid: str, request: Request):
        """Crea el pedido de venta en borrador en DISTRIPLUS (sale del almacén central) con las líneas elegidas."""
        u = current_user(request)
        cids, _ = scope(u, "all")
        b = await request.json()
        lineas = [l for l in b.get("lineas") or [] if l.get("product_id") and float(l.get("cantidad") or 0) > 0]
        if not b.get("partner_id") or not lineas:
            raise HTTPException(400, "Falta el cliente o las líneas")
        with con() as c:
            m = c.execute("SELECT asunto, de, fecha, texto FROM mensajes WHERE id=?", (mid,)).fetchone()

        def run():
            vals = {"partner_id": int(b["partner_id"]), "company_id": DISTRI, "client_order_ref": (m["asunto"] or "")[:60],
                    "note": (b.get("nota") or "") + f"\nPedido por correo de {m['de']} ({m['fecha']})",
                    "order_line": [[0, 0, {"product_id": int(l["product_id"]), "product_uom_qty": float(l["cantidad"])}] for l in lineas]}
            so = odoo.call("sale.order", "create", [DISTRI] + [x for x in cids if x != DISTRI], vals_list=[vals])
            so = so[0] if isinstance(so, list) else so
            try:
                odoo.call("sale.order", "message_post", [DISTRI], ids=[so], body=f"Correo del cliente ({m['de']}):<br><pre>{(m['texto'] or '')[:4000]}</pre>")
            except Exception:
                pass
            n = odoo.search_read("sale.order", [("id", "=", so)], ["name", "amount_untaxed"], company_ids=[DISTRI])[0]
            with lock, con() as c:
                c.execute("UPDATE mensajes SET pedido=?, estado='hecho', usuario=? WHERE id=?", (n["name"], u, mid))
            return {"pedido": n["name"], "id": so, "base": n["amount_untaxed"]}
        return guard(run)
