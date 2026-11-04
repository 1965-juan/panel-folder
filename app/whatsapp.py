"""WhatsApp: bandeja de conversaciones, bot con Claude y campañas.

Funciona con la API oficial de WhatsApp (Cloud API) a través de 360dialog o directamente con Meta.
El número sigue en el móvil del almacén (coexistencia): lo que se contesta desde el móvil
llega aquí como "eco" y el bot se aparta de esa conversación unas horas.

Ficheros en /data (los crea Juan, nunca van en el código):
  whatsapp_clave.txt   clave de 360dialog (D360-API-KEY) o token de Meta
  claude_clave.txt     clave de la API de Claude (sk-ant-...)
Variables de entorno opcionales:
  WA_PROVIDER   360dialog (por defecto) | meta
  WA_PHONE_ID   solo con Meta: id del número
  WA_MODEL      modelo de Claude (por defecto claude-sonnet-5-5)
"""
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
from fastapi.responses import PlainTextResponse

PAUSA_HUMANO = 12 * 3600          # el bot no habla en una conversación que lleva una persona
MAX_HISTORIAL = 20
BAJA_RE = re.compile(r"^\s*(baja|stop|no quiero (recibir|m[aá]s)|darme de baja)\b", re.I)
ALTA_RE = re.compile(r"^\s*(alta|quiero recibir ofertas|ofertas s[ií])\b", re.I)

INSTRUCCIONES_BASE = """Eres el asistente de WhatsApp de Folder Castellón (material de oficina, papelería, \
consumibles, regalo de empresa y artículos personalizados con logo).
Hablas en español (o en valenciano si el cliente te escribe en valenciano), con un tono cercano y profesional, \
frases cortas como en un WhatsApp, sin listas largas ni formato raro.

Qué puedes hacer:
- Contestar dudas generales sobre la empresa con la información de abajo.
- Buscar artículos en el catálogo con la herramienta buscar_articulos y decir si los tenemos y si hay stock.
- Mirar cómo va un pedido con la herramienta estado_pedidos (solo los del propio cliente, buscado por su teléfono).
- Recoger lo necesario para un presupuesto: qué artículo, cantidad, si lleva logo/personalización, plazo, \
nombre de empresa y email. Cuando lo tengas, usa pasar_a_persona con un resumen.

Reglas:
- No inventes precios, plazos ni stock: usa las herramientas o di que lo consultas con el equipo.
- {regla_precios}
- Si el cliente se queja, está enfadado, pide hablar con alguien o no sabes la respuesta, usa pasar_a_persona \
y dile que en breve le contesta una persona del equipo.
- Nunca hables de otras empresas del grupo ni de datos internos.

Información de la empresa (la completa Juan desde el panel):
{info}
"""
INFO_DEFECTO = "Web: www.foldercastellon.com\n(Horario, dirección y tiendas: pendiente de completar en Ajustes del bot.)"


def setup(app, *, data_dir: Path, odoo, current_user, es_admin, demo):
    db_path = data_dir / "whatsapp.db"
    lock = threading.Lock()
    clave_wa_f = data_dir / "whatsapp_clave.txt"
    clave_claude_f = data_dir / "claude_clave.txt"
    provider = os.environ.get("WA_PROVIDER", "360dialog").lower()
    phone_id = os.environ.get("WA_PHONE_ID", "")
    model = os.environ.get("WA_MODEL", "claude-sonnet-5-5")
    http = httpx.Client(timeout=60)

    def con():
        c = sqlite3.connect(db_path, timeout=20)
        c.row_factory = sqlite3.Row
        c.executescript("""
        CREATE TABLE IF NOT EXISTS contactos (wa_id TEXT PRIMARY KEY, nombre TEXT, optin INTEGER DEFAULT 0,
            bot INTEGER DEFAULT 1, humano_hasta REAL DEFAULT 0, necesita_persona INTEGER DEFAULT 0,
            resumen TEXT, ultimo REAL DEFAULT 0, ultimo_entrante REAL DEFAULT 0, no_leidos INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS mensajes (id INTEGER PRIMARY KEY AUTOINCREMENT, wa_msg_id TEXT, wa_id TEXT,
            dir TEXT, autor TEXT, texto TEXT, ts REAL, estado TEXT, campana INTEGER);
        CREATE INDEX IF NOT EXISTS m_wa ON mensajes(wa_id, ts);
        CREATE INDEX IF NOT EXISTS m_msg ON mensajes(wa_msg_id);
        CREATE TABLE IF NOT EXISTS campanas (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT, plantilla TEXT,
            idioma TEXT, variables TEXT, creada REAL, estado TEXT, total INTEGER DEFAULT 0, enviados INTEGER DEFAULT 0,
            fallidos INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS ajustes (k TEXT PRIMARY KEY, v TEXT);
        """)
        return c

    def ajuste(k, defecto=""):
        with con() as c:
            r = c.execute("SELECT v FROM ajustes WHERE k=?", (k,)).fetchone()
        return r["v"] if r else defecto

    def set_ajuste(k, v):
        with lock, con() as c:
            c.execute("INSERT OR REPLACE INTO ajustes VALUES (?,?)", (k, v))

    # secreto de la URL del webhook (la URL pública lleva este código para que nadie más pueda escribir)
    if not ajuste("webhook_secreto"):
        set_ajuste("webhook_secreto", secrets.token_urlsafe(18))

    def leer_clave(f, patron):
        if not f.exists():
            return ""
        m = re.findall(patron, f.read_text(errors="ignore"))
        return m[0] if m else ""

    def clave_wa():
        return leer_clave(clave_wa_f, r"[A-Za-z0-9_\-]{20,}")

    def clave_claude():
        return leer_clave(clave_claude_f, r"sk-ant-[A-Za-z0-9_\-]+")

    def conectado():
        return bool(clave_wa()) and not demo

    # ---------- envío ----------
    def api_wa(method, path, **kw):
        if provider == "meta":
            base, hdr = f"https://graph.facebook.com/v23.0/{phone_id}", {"Authorization": f"Bearer {clave_wa()}"}
        else:
            base, hdr = "https://waba-v2.360dialog.io", {"D360-API-KEY": clave_wa()}
        r = http.request(method, base + path, headers=hdr, **kw)
        if r.status_code >= 300:
            raise HTTPException(502, f"WhatsApp respondió {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def guardar(wa_id, dir_, autor, texto, wa_msg_id=None, estado=None, campana=None, ts=None):
        ts = ts or time.time()
        with lock, con() as c:
            c.execute("INSERT OR IGNORE INTO contactos (wa_id) VALUES (?)", (wa_id,))
            c.execute("INSERT INTO mensajes (wa_msg_id, wa_id, dir, autor, texto, ts, estado, campana) VALUES (?,?,?,?,?,?,?,?)",
                      (wa_msg_id, wa_id, dir_, autor, texto, ts, estado, campana))
            c.execute("UPDATE contactos SET ultimo=? WHERE wa_id=?", (ts, wa_id))
            if dir_ == "in":
                c.execute("UPDATE contactos SET ultimo_entrante=?, no_leidos=no_leidos+1 WHERE wa_id=?", (ts, wa_id))

    def enviar_texto(wa_id, texto, autor):
        mid = None
        if conectado():
            r = api_wa("POST", "/messages", json={"messaging_product": "whatsapp", "recipient_type": "individual",
                                                  "to": wa_id, "type": "text", "text": {"body": texto}})
            mid = (r.get("messages") or [{}])[0].get("id")
        guardar(wa_id, "out", autor, texto, mid, "enviado" if conectado() else "demo")

    def enviar_plantilla(wa_id, plantilla, idioma, variables, campana):
        comps = []
        if variables:
            comps = [{"type": "body", "parameters": [{"type": "text", "text": v} for v in variables]}]
        r = api_wa("POST", "/messages", json={"messaging_product": "whatsapp", "to": wa_id, "type": "template",
                                              "template": {"name": plantilla, "language": {"code": idioma}, "components": comps}})
        mid = (r.get("messages") or [{}])[0].get("id")
        guardar(wa_id, "out", "campaña", f"[Plantilla {plantilla}] " + " · ".join(variables), mid, "enviado", campana)

    # ---------- Odoo para el bot ----------
    def t_buscar(q):
        prods = odoo.search_read("product.product", ["&", ("sale_ok", "=", True), "|", ("name", "ilike", q), ("default_code", "ilike", q)],
                                 ["default_code", "name", "lst_price", "qty_available"], limit=8) if q else []
        precios = ajuste("bot_precios", "no") == "si"
        out = []
        for p in prods:
            d = {"ref": p.get("default_code") or "", "nombre": p["name"],
                 "stock": "sí" if (p.get("qty_available") or 0) > 0 else "consultar"}
            if precios:
                d["precio_sin_iva"] = p.get("lst_price")
            out.append(d)
        return out or "No he encontrado artículos con ese nombre."

    def t_pedidos(wa_id):
        tel = wa_id[-9:]
        partners = odoo.search_read("res.partner", ["|", ("phone", "ilike", tel), ("mobile", "ilike", tel)], ["id"], limit=5)
        if not partners:
            return "No encuentro ningún cliente con este número de teléfono."
        ids = [p["id"] for p in partners]
        ords = odoo.search_read("sale.order", [("partner_id", "child_of", ids), ("state", "in", ["sale", "sent"])],
                                ["name", "date_order", "state", "delivery_status", "amount_total"], limit=5, order="date_order desc")
        txt = {"pending": "pendiente de servir", "partial": "servido en parte", "full": "servido", "started": "en preparación"}
        return [{"pedido": o["name"], "fecha": (o.get("date_order") or "")[:10],
                 "estado": "presupuesto enviado" if o["state"] == "sent" else txt.get(o.get("delivery_status") or "", "confirmado")}
                for o in ords] or "No veo pedidos recientes de este cliente."

    HERRAMIENTAS = [
        {"name": "buscar_articulos", "description": "Busca artículos del catálogo por nombre o referencia.",
         "input_schema": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]}},
        {"name": "estado_pedidos", "description": "Pedidos recientes del cliente que escribe (buscado por su teléfono).",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "pasar_a_persona", "description": "Avisa al equipo para que conteste una persona. Incluye un resumen de lo que necesita el cliente.",
         "input_schema": {"type": "object", "properties": {"resumen": {"type": "string"}}, "required": ["resumen"]}},
    ]

    def instrucciones():
        regla = ("Puedes dar el precio orientativo sin IVA que devuelve buscar_articulos, aclarando que el precio final "
                 "depende de cantidad y personalización.") if ajuste("bot_precios", "no") == "si" else \
                "No des precios: di que le preparamos un presupuesto y recoge los datos."
        return INSTRUCCIONES_BASE.format(regla_precios=regla, info=ajuste("bot_info", INFO_DEFECTO))

    def claude(messages):
        r = http.post(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages", headers={
            "x-api-key": clave_claude(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 600, "system": instrucciones(), "tools": HERRAMIENTAS, "messages": messages})
        if r.status_code != 200:
            raise RuntimeError(f"Claude {r.status_code}: {r.text[:200]}")
        return r.json()

    def responder_bot(wa_id):
        with con() as c:
            ct = c.execute("SELECT * FROM contactos WHERE wa_id=?", (wa_id,)).fetchone()
            hist = c.execute("SELECT dir, texto FROM mensajes WHERE wa_id=? ORDER BY ts DESC LIMIT ?", (wa_id, MAX_HISTORIAL)).fetchall()
        if ajuste("bot_activo", "si") != "si" or not ct["bot"] or ct["humano_hasta"] > time.time():
            return
        if not clave_claude():
            if demo:
                enviar_texto(wa_id, "(demo) Aquí contestaría el bot cuando esté la clave de Claude.", "bot")
            return
        msgs = []
        for h in reversed(hist):
            role = "user" if h["dir"] == "in" else "assistant"
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"] += "\n" + (h["texto"] or "")
            else:
                msgs.append({"role": role, "content": h["texto"] or ""})
        while msgs and msgs[0]["role"] != "user":
            msgs.pop(0)
        if not msgs or msgs[-1]["role"] != "user":
            return
        try:
            for _ in range(5):
                res = claude(msgs)
                usos = [b for b in res["content"] if b["type"] == "tool_use"]
                if not usos:
                    texto = "".join(b.get("text", "") for b in res["content"] if b["type"] == "text").strip()
                    if texto:
                        enviar_texto(wa_id, texto, "bot")
                    return
                msgs.append({"role": "assistant", "content": res["content"]})
                resultados = []
                for u in usos:
                    try:
                        if u["name"] == "buscar_articulos":
                            out = t_buscar(u["input"].get("texto", ""))
                        elif u["name"] == "estado_pedidos":
                            out = t_pedidos(wa_id)
                        else:
                            with lock, con() as c:
                                c.execute("UPDATE contactos SET necesita_persona=1, resumen=? WHERE wa_id=?",
                                          (u["input"].get("resumen", ""), wa_id))
                            out = "Aviso enviado al equipo."
                    except Exception as e:
                        out = f"Error consultando: {e}"
                    resultados.append({"type": "tool_result", "tool_use_id": u["id"], "content": json.dumps(out, ensure_ascii=False)})
                msgs.append({"role": "user", "content": resultados})
        except Exception as e:
            print("[whatsapp] bot:", e, flush=True)
            with lock, con() as c:
                c.execute("UPDATE contactos SET necesita_persona=1, resumen=? WHERE wa_id=?", ("El bot no ha podido contestar" + (" (la clave de Claude no es válida)" if "401" in str(e) else "") + ": contesta tú.", wa_id))

    def entrante(wa_id, nombre, texto, wa_msg_id, ts):
        with con() as c:
            if wa_msg_id and c.execute("SELECT 1 FROM mensajes WHERE wa_msg_id=?", (wa_msg_id,)).fetchone():
                return  # repetido
        guardar(wa_id, "in", "cliente", texto, wa_msg_id, None, None, ts)
        with lock, con() as c:
            if nombre:
                c.execute("UPDATE contactos SET nombre=? WHERE wa_id=?", (nombre, wa_id))
        if BAJA_RE.match(texto or ""):
            with lock, con() as c:
                c.execute("UPDATE contactos SET optin=0 WHERE wa_id=?", (wa_id,))
            enviar_texto(wa_id, "Hecho, ya no te enviaremos ofertas por WhatsApp. Si nos escribes te seguiremos atendiendo igual 🙂", "bot")
            return
        if ALTA_RE.match(texto or ""):
            with lock, con() as c:
                c.execute("UPDATE contactos SET optin=1 WHERE wa_id=?", (wa_id,))
            enviar_texto(wa_id, "¡Genial! Te avisaremos de ofertas y novedades. Puedes escribir BAJA cuando quieras.", "bot")
            return
        threading.Thread(target=responder_bot, args=(wa_id,), daemon=True).start()

    def procesar(payload):
        for entry in payload.get("entry", []):
            for ch in entry.get("changes", []):
                v = ch.get("value", {})
                nombres = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in v.get("contacts", [])}
                for m in v.get("messages", []):
                    t = m.get("type")
                    texto = (m.get("text") or {}).get("body") if t == "text" else \
                        (m.get("button") or {}).get("text") or ((m.get("interactive") or {}).get("button_reply") or {}).get("title") or \
                        (m.get(t) or {}).get("caption") or f"[{t}]"
                    entrante(m["from"], nombres.get(m["from"]), texto, m.get("id"), float(m.get("timestamp") or time.time()))
                # coexistencia: lo que se escribe desde el móvil del almacén
                for m in v.get("message_echoes", []):
                    wa_id = m.get("to")
                    texto = (m.get("text") or {}).get("body") or f"[{m.get('type')}]"
                    with con() as c:
                        if c.execute("SELECT 1 FROM mensajes WHERE wa_msg_id=?", (m.get("id"),)).fetchone():
                            continue
                    guardar(wa_id, "out", "móvil", texto, m.get("id"), "enviado", None, float(m.get("timestamp") or time.time()))
                    with lock, con() as c:
                        c.execute("UPDATE contactos SET humano_hasta=?, necesita_persona=0 WHERE wa_id=?", (time.time() + PAUSA_HUMANO, wa_id))
                for s in v.get("statuses", []):
                    with lock, con() as c:
                        row = c.execute("SELECT campana, estado FROM mensajes WHERE wa_msg_id=?", (s.get("id"),)).fetchone()
                        c.execute("UPDATE mensajes SET estado=? WHERE wa_msg_id=?", (s.get("status"), s.get("id")))
                        if row and row["campana"] and s.get("status") == "failed" and row["estado"] != "failed":
                            c.execute("UPDATE campanas SET fallidos=fallidos+1 WHERE id=?", (row["campana"],))

    # ---------- webhook público ----------
    @app.get("/wa/webhook/{codigo}")
    def wa_verificar(codigo: str, request: Request):
        if codigo != ajuste("webhook_secreto"):
            raise HTTPException(404)
        return PlainTextResponse(request.query_params.get("hub.challenge", "ok"))

    @app.post("/wa/webhook/{codigo}")
    async def wa_webhook(codigo: str, request: Request):
        if codigo != ajuste("webhook_secreto"):
            raise HTTPException(404)
        payload = await request.json()
        threading.Thread(target=procesar, args=(payload,), daemon=True).start()
        return {"ok": True}

    # ---------- API del panel ----------
    def solo_admin(request):
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "Solo Juan")
        return u

    @app.get("/api/wa/estado")
    def wa_estado(request: Request):
        current_user(request)
        with con() as c:
            pend = c.execute("SELECT COUNT(*) n FROM contactos WHERE necesita_persona=1 OR no_leidos>0").fetchone()["n"]
        return {"conectado": conectado(), "demo": demo, "claude": bool(clave_claude()), "proveedor": provider,
                "pendientes": pend, "bot_activo": ajuste("bot_activo", "si") == "si"}

    @app.get("/api/wa/conversaciones")
    def wa_convs(request: Request, q: str = ""):
        current_user(request)
        with con() as c:
            rows = c.execute("""SELECT ct.*, (SELECT texto FROM mensajes m WHERE m.wa_id=ct.wa_id ORDER BY ts DESC LIMIT 1) ultimo_texto
                                FROM contactos ct WHERE ct.ultimo>0 AND (?='' OR ct.wa_id LIKE ? OR ct.nombre LIKE ?)
                                ORDER BY ct.necesita_persona DESC, ct.ultimo DESC LIMIT 200""", (q, f"%{q}%", f"%{q}%")).fetchall()
        ahora = time.time()
        return [dict(r, humano=r["humano_hasta"] > ahora, ventana=ahora - r["ultimo_entrante"] < 24 * 3600) for r in rows]

    @app.get("/api/wa/conversaciones/{wa_id}")
    def wa_conv(wa_id: str, request: Request):
        current_user(request)
        with lock, con() as c:
            c.execute("UPDATE contactos SET no_leidos=0 WHERE wa_id=?", (wa_id,))
        with con() as c:
            ct = c.execute("SELECT * FROM contactos WHERE wa_id=?", (wa_id,)).fetchone()
            ms = c.execute("SELECT * FROM mensajes WHERE wa_id=? ORDER BY ts", (wa_id,)).fetchall()
        if not ct:
            raise HTTPException(404)
        ahora = time.time()
        return {"contacto": dict(ct, humano=ct["humano_hasta"] > ahora, ventana=ahora - ct["ultimo_entrante"] < 24 * 3600),
                "mensajes": [dict(m) for m in ms]}

    @app.post("/api/wa/conversaciones/{wa_id}/enviar")
    async def wa_enviar(wa_id: str, request: Request):
        u = current_user(request)
        body = await request.json()
        texto = (body.get("texto") or "").strip()
        if not texto:
            raise HTTPException(400, "Mensaje vacío")
        with con() as c:
            ct = c.execute("SELECT ultimo_entrante FROM contactos WHERE wa_id=?", (wa_id,)).fetchone()
        if conectado() and (not ct or time.time() - ct["ultimo_entrante"] > 24 * 3600):
            raise HTTPException(400, "Han pasado más de 24 h desde su último mensaje: WhatsApp solo deja escribirle con una plantilla aprobada.")
        enviar_texto(wa_id, texto, u)
        with lock, con() as c:
            c.execute("UPDATE contactos SET humano_hasta=?, necesita_persona=0 WHERE wa_id=?", (time.time() + PAUSA_HUMANO, wa_id))
        return {"ok": True}

    @app.post("/api/wa/conversaciones/{wa_id}/ajustes")
    async def wa_conv_ajustes(wa_id: str, request: Request):
        current_user(request)
        b = await request.json()
        with lock, con() as c:
            if "bot" in b:
                c.execute("UPDATE contactos SET bot=?, humano_hasta=0 WHERE wa_id=?", (1 if b["bot"] else 0, wa_id))
            if "optin" in b:
                c.execute("UPDATE contactos SET optin=? WHERE wa_id=?", (1 if b["optin"] else 0, wa_id))
            if "atendido" in b:
                c.execute("UPDATE contactos SET necesita_persona=0 WHERE wa_id=?", (wa_id,))
        return {"ok": True}

    @app.post("/api/wa/simular")
    async def wa_simular(request: Request):
        """Solo en demo o sin conexión: mete un mensaje como si lo enviara un cliente, para probar el bot."""
        solo_admin(request)
        if conectado():
            raise HTTPException(400, "Con WhatsApp conectado no se simula")
        b = await request.json()
        entrante(b.get("wa_id") or "34600000000", b.get("nombre") or "Cliente de prueba", b.get("texto") or "Hola", None, time.time())
        time.sleep(0.2)
        return {"ok": True}

    @app.get("/api/wa/ajustes")
    def wa_ajustes(request: Request):
        solo_admin(request)
        host = request.headers.get("x-forwarded-host") or request.url.netloc
        return {"bot_activo": ajuste("bot_activo", "si"), "bot_precios": ajuste("bot_precios", "no"),
                "bot_info": ajuste("bot_info", INFO_DEFECTO), "modelo": model,
                "webhook_ruta": f"/wa/webhook/{ajuste('webhook_secreto')}", "host": host}

    @app.put("/api/wa/ajustes")
    async def wa_ajustes_put(request: Request):
        solo_admin(request)
        b = await request.json()
        for k in ("bot_activo", "bot_precios", "bot_info"):
            if k in b:
                set_ajuste(k, str(b[k]))
        return {"ok": True}

    @app.post("/api/wa/registrar-webhook")
    async def wa_registrar(request: Request):
        """Con 360dialog: le dice a WhatsApp a qué dirección pública tiene que mandar los mensajes."""
        solo_admin(request)
        b = await request.json()
        base = (b.get("url_publica") or "").rstrip("/")
        if not base.startswith("https://"):
            raise HTTPException(400, "La dirección pública tiene que empezar por https://")
        if provider != "360dialog":
            raise HTTPException(400, "Con Meta directo el webhook se pone en el panel de Meta")
        url = f"{base}/wa/webhook/{ajuste('webhook_secreto')}"
        api_wa("POST", "/v1/configs/webhook", json={"url": url})
        set_ajuste("webhook_url", url)
        return {"ok": True}

    # ---------- campañas ----------
    @app.get("/api/wa/plantillas")
    def wa_plantillas(request: Request):
        solo_admin(request)
        if not conectado():
            return [{"name": "oferta_navidad", "language": "es", "status": "APPROVED", "category": "MARKETING",
                     "texto": "Hola {{1}}, esta Navidad tenemos {{2}}. ¿Te preparamos presupuesto? Responde BAJA para no recibir más."}]
        path = "/v1/configs/templates" if provider == "360dialog" else "/message_templates"
        d = api_wa("GET", path)
        out = []
        for t in d.get("waba_templates") or d.get("data") or []:
            body = next((c.get("text") for c in t.get("components", []) if c.get("type") == "BODY"), "")
            out.append({"name": t["name"], "language": t.get("language"), "status": t.get("status"),
                        "category": t.get("category"), "texto": body})
        return out

    @app.get("/api/wa/destinatarios")
    def wa_destinatarios(request: Request):
        solo_admin(request)
        with con() as c:
            rows = c.execute("SELECT wa_id, nombre FROM contactos WHERE optin=1 ORDER BY nombre").fetchall()
        return [dict(r) for r in rows]

    @app.post("/api/wa/contactos/importar")
    async def wa_importar(request: Request):
        """Alta de contactos que han dado su permiso (pega una lista: teléfono;nombre por línea)."""
        solo_admin(request)
        b = await request.json()
        if not b.get("consentimiento"):
            raise HTTPException(400, "Hay que confirmar que estos contactos han aceptado recibir ofertas por WhatsApp")
        n = 0
        with lock, con() as c:
            for linea in (b.get("lista") or "").splitlines():
                partes = re.split(r"[;,\t]", linea, maxsplit=1)
                tel = re.sub(r"\D", "", partes[0])
                if len(tel) == 9:
                    tel = "34" + tel
                if len(tel) < 11:
                    continue
                nombre = partes[1].strip() if len(partes) > 1 else None
                c.execute("INSERT OR IGNORE INTO contactos (wa_id, nombre) VALUES (?,?)", (tel, nombre))
                c.execute("UPDATE contactos SET optin=1, nombre=COALESCE(nombre, ?) WHERE wa_id=?", (nombre, tel))
                n += 1
        return {"ok": True, "importados": n}

    @app.get("/api/wa/campanas")
    def wa_campanas(request: Request):
        solo_admin(request)
        with con() as c:
            rows = c.execute("SELECT * FROM campanas ORDER BY creada DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]

    def lanzar(cid, plantilla, idioma, variables, destinos):
        enviados = fallidos = 0
        for d in destinos:
            vs = [v.replace("{nombre}", (d["nombre"] or "").split(" ")[0] or "") for v in variables]
            try:
                if conectado():
                    enviar_plantilla(d["wa_id"], plantilla, idioma, vs, cid)
                else:
                    guardar(d["wa_id"], "out", "campaña", f"[Plantilla {plantilla}] " + " · ".join(vs), None, "demo", cid)
                enviados += 1
            except Exception as e:
                print("[whatsapp] campaña:", e, flush=True)
                fallidos += 1
            with lock, con() as c:
                c.execute("UPDATE campanas SET enviados=?, fallidos=? WHERE id=?", (enviados, fallidos, cid))
            time.sleep(1.2)  # sin prisas: WhatsApp penaliza los envíos en ráfaga
        with lock, con() as c:
            c.execute("UPDATE campanas SET estado='terminada' WHERE id=?", (cid,))

    @app.post("/api/wa/campanas")
    async def wa_campana_nueva(request: Request):
        solo_admin(request)
        b = await request.json()
        plantilla, idioma = b.get("plantilla"), b.get("idioma") or "es"
        variables = b.get("variables") or []
        sel = set(b.get("destinatarios") or [])
        with con() as c:
            destinos = [dict(r) for r in c.execute("SELECT wa_id, nombre FROM contactos WHERE optin=1").fetchall()
                        if not sel or r["wa_id"] in sel]
        if not plantilla or not destinos:
            raise HTTPException(400, "Falta plantilla o no hay destinatarios con permiso")
        with lock, con() as c:
            cid = c.execute("INSERT INTO campanas (nombre, plantilla, idioma, variables, creada, estado, total) VALUES (?,?,?,?,?,?,?)",
                            (b.get("nombre") or plantilla, plantilla, idioma, json.dumps(variables), time.time(), "enviando", len(destinos))).lastrowid
        threading.Thread(target=lanzar, args=(cid, plantilla, idioma, variables, destinos), daemon=True).start()
        return {"ok": True, "id": cid, "total": len(destinos)}
