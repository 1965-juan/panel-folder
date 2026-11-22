"""Seguimiento del trabajo que entra por correo.
Lee las cuentas de correo_cuentas.txt (entrada y enviados) desde la fecha que se diga, clasifica con Claude lo que
escriben los clientes (pedido, petición de precio, aceptación…) y lo cruza con Odoo:
  A · Piden precio y no hay presupuesto      C · Piden material y no hay pedido
  B · Presupuesto enviado sin respuesta      D · Pedido sin servir      E · Servido sin facturar
Cada cosa se puede marcar como hecha, descartar o dejar con un recordatorio para un día.
Los PDF de los correos no se guardan aquí (eso lo hace el módulo Correo con los correos nuevos).
"""
import datetime as dt
import email
import imaplib
import json
import os
import re
import sqlite3
import threading
import time
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

import httpx
from fastapi import HTTPException, Request

EMPRESAS = [1, 4, 5]
PROPIOS = {"foldercastellon.es", "foldercs.es", "regalosfolder.es", "distripluslevante.es"}
GENERICOS = {"gmail.com", "hotmail.com", "hotmail.es", "outlook.com", "outlook.es", "yahoo.es", "yahoo.com", "icloud.com", "live.com", "msn.com",
             "telefonica.net", "gva.es", "folder.es", "me.com", "ymail.com", "terra.es", "ono.com", "googlemail.com", "dipcas.es"}
CLASES = ["pedido", "presupuesto", "acepta", "reclamacion", "proveedor", "admin", "otro"]
PIDE = {"pedido", "presupuesto", "acepta"}
TIPOS = {"A": "Piden precio y no hay presupuesto", "C": "Piden material y no hay pedido", "B": "Presupuesto enviado sin respuesta",
         "D": "Pedido sin servir", "E": "Servido sin facturar"}
CORTE = re.compile(r"^\s*(>|-{2,}\s*(mensaje original|original message|forwarded message|mensaje reenviado)|(de|from|enviado el|sent)\s*:.*|el .{5,80} escribi[oó]:|on .{5,80} wrote:)",
                   re.I | re.M)


def dec(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def norm_asunto(s):
    s = (s or "").strip()
    while True:
        n = re.sub(r"^\s*(re|rv|fw|fwd|reenv|enc|aw|tr)\s*[:：]\s*|^\s*\[[^\]]{0,30}\]\s*", "", s, flags=re.I)
        if n == s:
            break
        s = n
    return re.sub(r"\s+", " ", s).strip().lower()


def es_respuesta(s):
    return bool(re.match(r"^\s*(re|rv|fw|fwd|aw|enc)\s*[:：]", s or "", re.I))


def dominio(e):
    return (e or "").split("@")[-1].lower()


def setup(app, *, data_dir: Path, odoo, current_user, scope, guard, es_admin):
    db = data_dir / "seguimiento.db"
    cuentas_f = data_dir / "correo_cuentas.txt"
    lock = threading.Lock()
    http = httpx.Client(timeout=180)
    modelo = os.environ.get("PANEL_MODEL_RAPIDO", "claude-haiku-5-5")
    EST = {"leyendo": False, "fase": "", "cuentas": {}, "clasificados": 0, "por_clasificar": 0, "error": "", "inicio": 0, "fin": 0}
    CACHE = {"t": 0, "datos": None, "calculando": False, "error": ""}

    def con():
        c = sqlite3.connect(db, timeout=30)
        c.row_factory = sqlite3.Row
        c.executescript("""
        CREATE TABLE IF NOT EXISTS hist (id TEXT PRIMARY KEY, cuenta TEXT, carpeta TEXT, enviado INTEGER, msgid TEXT, inreply TEXT, refs TEXT,
          de TEXT, nombre TEXT, para TEXT, asunto TEXT, ts REAL, texto TEXT, adjuntos TEXT, lista INTEGER, clase TEXT, cli TEXT, ref TEXT, que TEXT);
        CREATE INDEX IF NOT EXISTS ix_h_ts ON hist(ts);
        CREATE INDEX IF NOT EXISTS ix_h_cl ON hist(clase);
        CREATE TABLE IF NOT EXISTS carpetas (cuenta TEXT, carpeta TEXT, uidvalidity TEXT, ultimo INTEGER, PRIMARY KEY(cuenta, carpeta));
        CREATE TABLE IF NOT EXISTS marcas (clave TEXT PRIMARY KEY, estado TEXT, recordar TEXT, nota TEXT, usuario TEXT, cuando REAL);
        CREATE TABLE IF NOT EXISTS ajustes (k TEXT PRIMARY KEY, v TEXT);
        """)
        return c

    con().close()

    def ajuste(k, v=None):
        with lock, con() as c:
            if v is None:
                r = c.execute("SELECT v FROM ajustes WHERE k=?", (k,)).fetchone()
                return r["v"] if r else None
            c.execute("INSERT OR REPLACE INTO ajustes VALUES (?,?)", (k, v))

    def leer_cuentas():
        out = []
        if not cuentas_f.exists():
            return out
        for l in cuentas_f.read_text(encoding="utf-8", errors="ignore").splitlines():
            l = l.strip()
            if not l or l.startswith("#"):
                continue
            p = [x.strip() for x in l.split(";")]
            if len(p) >= 3:
                out.append({"usuario": p[0], "clave": p[1], "servidor": p[2], "puerto": int(p[3]) if len(p) > 3 and p[3].isdigit() else 993})
        return out

    def clave_claude():
        f = data_dir / "claude_clave.txt"
        k = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", f.read_text(errors="ignore")) if f.exists() else []
        if not k:
            raise RuntimeError("Falta la clave de Claude en el NAS")
        return k[0]

    # ---------------- lectura IMAP ----------------
    def carpetas_de(M):
        typ, lst = M.list()
        entrada, enviados = "INBOX", None
        for raw in lst or []:
            s = raw.decode(errors="ignore") if isinstance(raw, bytes) else str(raw)
            m = re.match(r'\((?P<f>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<n>.+)$', s)
            if not m:
                continue
            n = m.group("n").strip()
            nombre = n.strip('"')
            if "\\Sent" in m.group("f") or (enviados is None and re.search(r"(^|[./])(sent|enviados|sent items|sent messages|elementos enviados)$", nombre, re.I)):
                enviados = nombre
        return [(entrada, 0)] + ([(enviados, 1)] if enviados else [])

    def texto_de(msg):
        texto, html, adj = "", "", []
        for part in msg.walk():
            if part.is_multipart():
                continue
            fn = part.get_filename()
            ct = part.get_content_type()
            if fn or part.get_content_disposition() == "attachment":
                adj.append(dec(fn) or "adjunto")
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                t = payload.decode(part.get_content_charset() or "utf-8", "replace")
            except Exception:
                t = ""
            if ct == "text/plain" and not texto:
                texto = t
            elif ct == "text/html" and not html:
                html = t
        if not texto and html:
            texto = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.I)
            texto = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", texto, flags=re.I)
            texto = re.sub(r"<[^>]+>", " ", texto)
            texto = re.sub(r"&nbsp;", " ", texto)
            texto = re.sub(r"&[a-z]+;", " ", texto)
        texto = re.sub(r"[ \t]+", " ", texto)
        texto = re.sub(r"\n\s*\n+", "\n", texto).strip()
        return texto, adj

    def leer_carpeta(M, cta, carpeta, enviado, desde):
        typ, d = M.select(f'"{carpeta}"' if " " in carpeta and not carpeta.startswith('"') else carpeta, readonly=True)
        if typ != "OK":
            return 0
        uv = ""
        try:
            uv = M.response("UIDVALIDITY")[1][0].decode()
        except Exception:
            pass
        with con() as c:
            st = c.execute("SELECT * FROM carpetas WHERE cuenta=? AND carpeta=?", (cta["usuario"], carpeta)).fetchone()
        if st and st["uidvalidity"] == uv and st["ultimo"]:
            typ, d = M.uid("search", None, f"UID {int(st['ultimo']) + 1}:*")
            uids = [int(x) for x in (d[0] or b"").split() if int(x) > int(st["ultimo"])]
        else:
            typ, d = M.uid("search", None, "SINCE " + desde.strftime("%d-%b-%Y"))
            uids = [int(x) for x in (d[0] or b"").split()]
        uids.sort()
        clave = f"{cta['usuario']} · {'enviados' if enviado else 'entrada'}"
        EST["cuentas"][clave] = {"total": len(uids), "leidos": 0}
        n = 0
        for i in range(0, len(uids), 40):
            trozo = uids[i:i + 40]
            typ, data = M.uid("fetch", ",".join(map(str, trozo)), "(UID BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.12000>)")
            lista_m, cur = [], None
            for x in data or []:
                pre = (x[0] if isinstance(x, tuple) else x) or b""
                pre = pre.decode(errors="ignore") if isinstance(pre, bytes) else str(pre)
                if isinstance(x, tuple) and re.match(r"^\d+ \(", pre):
                    cur = {"uid": None}
                    lista_m.append(cur)
                if cur is None:
                    continue
                mu = re.search(r"UID (\d+)", pre)
                if mu:
                    cur["uid"] = int(mu.group(1))
                if isinstance(x, tuple):
                    cur["h" if "HEADER" in pre else "t"] = x[1]
            filas = []
            for p in lista_m:
                uid = p.get("uid")
                if not uid:
                    continue
                try:
                    msg = email.message_from_bytes((p.get("h") or b"") + (p.get("t") or b""))
                    texto, adj = texto_de(msg)
                    try:
                        ts = parsedate_to_datetime(msg.get("Date")).timestamp()
                    except Exception:
                        ts = time.time()
                    de = getaddresses([dec(msg.get("From"))])
                    nombre, mail = (de[0] if de else ("", ""))
                    para = ", ".join(a for _, a in getaddresses([dec(msg.get("To")), dec(msg.get("Cc"))]) if a)
                    mc = CORTE.search(texto or "")
                    if mc and mc.start() > 0:
                        nuevo = texto[:mc.start()].strip() + "\n\n[mensaje citado]\n" + texto[mc.start():]
                    else:
                        nuevo = texto
                    lista = 1 if (msg.get("List-Unsubscribe") or msg.get("List-Id") or (msg.get("Precedence") or "").lower() in ("bulk", "list")
                                  or (msg.get("Auto-Submitted") or "no").lower() != "no") else 0
                    refs = " ".join(re.findall(r"<[^>]+>", msg.get("References") or ""))
                    filas.append((f"{cta['usuario']}|{carpeta}|{uv}|{uid}", cta["usuario"], carpeta, enviado, (msg.get("Message-ID") or "").strip(),
                                  (msg.get("In-Reply-To") or "").strip()[:300], refs[:2000], (mail or "").lower(), nombre or mail, para[:600],
                                  dec(msg.get("Subject"))[:300], ts, (nuevo or "")[:4000], json.dumps(adj, ensure_ascii=False), lista))
                except Exception:
                    continue
            with lock, con() as c:
                c.executemany("INSERT OR IGNORE INTO hist (id,cuenta,carpeta,enviado,msgid,inreply,refs,de,nombre,para,asunto,ts,texto,adjuntos,lista) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", filas)
                c.execute("INSERT OR REPLACE INTO carpetas VALUES (?,?,?,?)", (cta["usuario"], carpeta, uv, max(trozo)))
            n += len(filas)
            EST["cuentas"][clave]["leidos"] = min(len(uids), i + len(trozo))
        return n

    def leer_todo(desde):
        cuentas = leer_cuentas()
        if not cuentas:
            raise RuntimeError("No hay cuentas en correo_cuentas.txt (carpeta de datos del panel en el NAS)")
        for cta in cuentas:
            EST["fase"] = f"Leyendo {cta['usuario']}"
            try:
                M = imaplib.IMAP4_SSL(cta["servidor"], cta["puerto"], timeout=90)
                try:
                    M.login(cta["usuario"], cta["clave"])
                    for carpeta, env in carpetas_de(M):
                        leer_carpeta(M, cta, carpeta, env, desde)
                finally:
                    try:
                        M.logout()
                    except Exception:
                        pass
            except Exception as e:
                EST["cuentas"][cta["usuario"]] = {"error": str(e)[:200]}

    # ---------------- clasificar ----------------
    def propias():
        return {c["usuario"].lower() for c in leer_cuentas()}

    def candidatos(c):
        mias = propias()
        filas = c.execute("SELECT id, de, nombre, asunto, ts, texto, adjuntos FROM hist WHERE clase IS NULL AND enviado=0 AND lista=0 ORDER BY ts").fetchall()
        fuera, buenos = [], []
        for f in filas:
            d = f["de"] or ""
            if d in mias or re.match(r"(no-?reply|noreply|mailer-daemon|postmaster|notificaciones?|notifications?|newsletter|info@mail)", d) or "@" not in d:
                fuera.append(f["id"])
            else:
                buenos.append(f)
        if fuera:
            c.executemany("UPDATE hist SET clase='otro', que='automático' WHERE id=?", [(x,) for x in fuera])
        return buenos

    INSTR = ('{"r":[{"i":0,"c":"' + "|".join(CLASES) + '","cli":"empresa o persona cliente si se ve","ref":"nº de pedido del cliente (ORD…, nº pedido) o nuestro nº (S0…, N31/…) si aparece",'
             '"que":"qué quiere el cliente en concreto: artículos, cantidades, plazo (25 palabras como máximo)"}]}.\n'
             "LEE EL TEXTO ENTERO: el asunto muchas veces no dice nada («Hola», «Consulta», el nombre de la empresa o vacío); lo que cuenta es lo que pide en el cuerpo o en el PDF adjunto. "
             "pedido = un cliente nos pide material o un trabajo (aunque no use la palabra pedido: «necesitamos», «mandadnos», «nos hace falta», una lista de artículos, un PDF de pedido); "
             "presupuesto = un cliente nos pide precio, oferta, cuánto costaría o disponibilidad; acepta = un cliente acepta un presupuesto, da el visto bueno a una prueba o dice «adelante»; "
             "reclamacion = queja o error de un cliente; proveedor = un proveedor nos escribe (facturas, tarifas, ofertas, envíos); admin = bancos, recibos, gestoría, administración; otro = lo demás. "
             "Clasifica por lo que escribe el remitente antes de [mensaje citado]; si es un reenvío interno (de otra tienda Folder, de pedidos@ o un Fwd:) clasifícalo por lo que pide el cliente del correo reenviado.")

    def llama(content, max_tokens):
        body = {"model": modelo, "max_tokens": max_tokens,
                "system": "Clasificas correos que recibe Folder Castellón (papelería, material de oficina, imprenta y regalo publicitario; empresas Distriplus, Logipaper, Ingraf). "
                          "Responde SOLO un JSON.",
                "messages": [{"role": "user", "content": content}]}
        r = http.post(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages",
                      headers={"x-api-key": clave_claude(), "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"Claude respondió {r.status_code}: {r.text[:200]}")
        t = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        m = re.search(r"\{[\s\S]*\}", t)
        return json.loads(m.group(0)).get("r", []) if m else []

    def cabecera(i, f):
        adj = ", ".join(json.loads(f["adjuntos"] or "[]"))[:200]
        return (f"[{i}] De: {f['nombre']} <{f['de']}> · {dt.datetime.fromtimestamp(f['ts']).strftime('%d/%m/%Y')}\nAsunto: {f['asunto'] or '(sin asunto)'}\n"
                + (f"Adjuntos: {adj}\n" if adj else ""))

    def fila_res(x, f):
        cl = x.get("c") if x.get("c") in CLASES else "otro"
        return (cl, (x.get("cli") or "")[:120], (x.get("ref") or "")[:80], (x.get("que") or "")[:300], f["id"])

    def clasificar_pdf(f, pdf):
        import base64
        res = llama([{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(pdf).decode()}},
                     {"type": "text", "text": cabecera(0, f) + (f["texto"] or "")[:2500] + "\n\n---\nEl PDF de arriba va adjunto a este correo. Devuelve " + INSTR}], 800)
        return [fila_res(x, f) for x in res[:1]]

    def pdfs_de(filas):
        """Vuelve a abrir los correos (solo los que traen PDF y casi nada de texto) y saca el primer PDF de cada uno."""
        out, ctas = {}, {c["usuario"]: c for c in leer_cuentas()}
        grupos = {}
        for f in filas:
            try:
                cta, carpeta, uv, uid = f["id"].rsplit("|", 3)
            except ValueError:
                continue
            grupos.setdefault((cta, carpeta, uv), []).append((int(uid), f))
        for (cta, carpeta, uv), l in grupos.items():
            if cta not in ctas:
                continue
            c = ctas[cta]
            try:
                M = imaplib.IMAP4_SSL(c["servidor"], c["puerto"], timeout=90)
                M.login(c["usuario"], c["clave"])
                typ, _ = M.select(f'"{carpeta}"' if " " in carpeta else carpeta, readonly=True)
                try:
                    uv_ahora = M.response("UIDVALIDITY")[1][0].decode()
                except Exception:
                    uv_ahora = ""
                if typ != "OK" or uv_ahora != uv:
                    M.logout()
                    continue
                for uid, f in l:
                    try:
                        typ, d = M.uid("fetch", str(uid), "(RFC822.SIZE)")
                        cab = b" ".join((x[0] if isinstance(x, tuple) else x) for x in (d or []) if x)
                        mt = re.search(rb"RFC822\.SIZE (\d+)", cab)
                        if not mt or int(mt.group(1)) > 15_000_000:
                            continue
                        typ, d = M.uid("fetch", str(uid), "(BODY.PEEK[])")
                        raw = next((x[1] for x in d if isinstance(x, tuple)), None)
                        if not raw:
                            continue
                        for part in email.message_from_bytes(raw).walk():
                            fn = (dec(part.get_filename()) or "").lower()
                            if part.get_content_type() == "application/pdf" or fn.endswith(".pdf"):
                                datos = part.get_payload(decode=True) or b""
                                if 0 < len(datos) <= 8_000_000:
                                    out[f["id"]] = datos
                                    break
                    except Exception:
                        continue
                M.logout()
            except Exception:
                continue
        return out

    def clasificar_lote(lote):
        partes = [cabecera(i, f) + (f["texto"] or "")[:2000] for i, f in enumerate(lote)]
        res = llama("\n\n".join(partes) + "\n\n---\nPara cada correo devuelve " + INSTR, 5000)
        out = []
        for x in res:
            try:
                f = lote[int(x.get("i"))]
            except Exception:
                continue
            out.append(fila_res(x, f))
        return out

    def clasificar_todo():
        with lock, con() as c:
            lista = candidatos(c)
        EST["por_clasificar"] = len(lista)
        EST["clasificados"] = 0
        fallos = 0
        # los que traen un PDF y casi no escriben nada: se lee el PDF (el pedido suele venir ahí)
        con_pdf = [f for f in lista if any(a.lower().endswith(".pdf") for a in json.loads(f["adjuntos"] or "[]"))
                   and len((f["texto"] or "").split("[mensaje citado]")[0].strip()) < 400]
        if con_pdf:
            EST["fase"] = f"Leyendo los PDF adjuntos ({len(con_pdf)})"
            hechos = set()
            for i in range(0, len(con_pdf), 25):
                trozo = con_pdf[i:i + 25]
                pdfs = pdfs_de(trozo)
                for f in trozo:
                    if f["id"] not in pdfs:
                        continue
                    try:
                        res = clasificar_pdf(f, pdfs[f["id"]])
                    except Exception as e:
                        EST["error"] = str(e)[:200]
                        continue
                    if res:
                        with lock, con() as c:
                            c.executemany("UPDATE hist SET clase=?, cli=?, ref=?, que=? WHERE id=?", res)
                        hechos.add(f["id"])
                EST["clasificados"] = len(hechos)
            lista = [f for f in lista if f["id"] not in hechos]
        EST["fase"] = "Clasificando con Claude"
        base = EST["clasificados"]
        for i in range(0, len(lista), 12):
            lote = lista[i:i + 12]
            try:
                res = clasificar_lote(lote)
            except Exception as e:
                fallos += 1
                EST["error"] = str(e)[:200]
                if fallos > 5:
                    raise
                time.sleep(10)
                continue
            with lock, con() as c:
                c.executemany("UPDATE hist SET clase=?, cli=?, ref=?, que=? WHERE id=?", res)
            EST["clasificados"] = base + i + len(lote)

    def proceso(desde):
        if EST["leyendo"]:
            return
        EST.update({"leyendo": True, "error": "", "inicio": time.time(), "fin": 0})
        try:
            leer_todo(desde)
            clasificar_todo()
            EST["fase"] = "Cruzando con Odoo"
            calcular()
            EST["fase"] = "Hecho"
        except Exception as e:
            EST["error"] = str(e)[:300]
            EST["fase"] = "Error"
        finally:
            EST["leyendo"] = False
            EST["fin"] = time.time()

    def bucle():
        time.sleep(90)
        while True:
            try:
                d = ajuste("desde")
                if d and leer_cuentas() and not EST["leyendo"]:
                    proceso(dt.date.fromisoformat(d))
                elif not CACHE["datos"] or time.time() - CACHE["t"] > 1800:
                    calcular()
            except Exception:
                pass
            time.sleep(900)
    threading.Thread(target=bucle, daemon=True).start()

    # ---------------- hilos ----------------
    def hilos(filas):
        padre = {}

        def raiz(x):
            while padre.get(x, x) != x:
                padre[x] = padre.get(padre[x], padre[x])
                x = padre[x]
            return x

        def une(a, b):
            ra, rb = raiz(a), raiz(b)
            if ra != rb:
                padre[rb] = ra
        mias = propias()
        por_msgid = {}
        for f in filas:
            k = f["id"]
            padre.setdefault(k, k)
            if f["msgid"]:
                if f["msgid"] in por_msgid:
                    une(por_msgid[f["msgid"]], k)
                else:
                    por_msgid[f["msgid"]] = k
        for f in filas:
            for r in re.findall(r"<[^>]+>", (f["inreply"] or "") + " " + (f["refs"] or "")):
                if r in por_msgid:
                    une(por_msgid[r], f["id"])
        # respuestas sin cabeceras: mismo asunto y mismo interlocutor en 30 días
        ultimo = {}
        for f in sorted(filas, key=lambda x: x["ts"]):
            na = norm_asunto(f["asunto"])
            otros = [a for a in ([f["de"]] if not f["enviado"] else [a.strip().lower() for a in (f["para"] or "").split(",")]) if a and a not in mias]
            for o in otros:
                k = (na, o)
                if len(na) >= 4 and k in ultimo and es_respuesta(f["asunto"]) and f["ts"] - ultimo[k][1] < 30 * 86400:
                    une(ultimo[k][0], f["id"])
                ultimo[k] = (f["id"], f["ts"])
        return {f["id"]: raiz(f["id"]) for f in filas}

    # ---------------- cruce con Odoo ----------------
    def calcular():
        if CACHE["calculando"]:
            return
        CACHE["calculando"] = True
        try:
            CACHE["datos"] = _calcular()
            CACHE["t"] = time.time()
            CACHE["error"] = ""
        except Exception as e:
            CACHE["error"] = str(e)[:300]
        finally:
            CACHE["calculando"] = False

    def _calcular():
        desde = ajuste("desde") or "2026-01-01"
        t0 = dt.datetime.fromisoformat(desde).timestamp()
        mias = propias()
        with con() as c:
            filas = c.execute("SELECT id,cuenta,enviado,msgid,inreply,refs,de,nombre,para,asunto,ts,clase,cli,ref,que,texto FROM hist WHERE ts>=?",
                              (t0 - 20 * 86400,)).fetchall()
            marcas = {r["clave"]: dict(r) for r in c.execute("SELECT * FROM marcas")}
        H = hilos(filas) if filas else {}
        grupos = {}
        for f in filas:
            grupos.setdefault(H[f["id"]], []).append(f)
        # pedidos de venta de Odoo
        sos = odoo.search_read("sale.order", [("date_order", ">=", (dt.date.fromisoformat(desde) - dt.timedelta(days=20)).isoformat()),
                                              ("company_id", "in", EMPRESAS)],
                               ["name", "partner_id", "state", "date_order", "delivery_status", "invoice_status", "amount_untaxed", "amount_total",
                                "company_id", "client_order_ref", "effective_date", "write_date"], EMPRESAS)
        pids = sorted({s["partner_id"][0] for s in sos if s.get("partner_id")})
        com = {}
        for i in range(0, len(pids), 500):
            for p in odoo.search_read("res.partner", [("id", "in", pids[i:i + 500]), ("active", "in", [True, False])], ["commercial_partner_id"], EMPRESAS):
                com[p["id"]] = p["commercial_partner_id"][0] if p.get("commercial_partner_id") else p["id"]
        por_com = {}
        for s in sos:
            s["com"] = com.get(s["partner_id"][0]) if s.get("partner_id") else None
            s["ts"] = dt.datetime.fromisoformat(s["date_order"].replace(" ", "T")).timestamp() if s.get("date_order") else 0
            por_com.setdefault(s["com"], []).append(s)
        # clientes por email
        correos = set()
        for g in grupos.values():
            for f in g:
                if not f["enviado"] and f["de"] and f["de"] not in mias:
                    correos.add(f["de"])
                for e in re.findall(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", f["texto"] or "")[:6]:
                    if dominio(e) not in PROPIOS:
                        correos.add(e.lower())
        por_mail, por_dom = {}, {}
        doms = sorted({dominio(e) for e in correos if dominio(e) not in GENERICOS and dominio(e) not in PROPIOS})
        lista = sorted(correos)
        dominio_q = []
        for i in range(0, len(lista), 200):
            dominio_q = ["|"] * (len(lista[i:i + 200]) - 1) + [("email", "=ilike", e) for e in lista[i:i + 200]]
            for p in odoo.search_read("res.partner", dominio_q, ["email", "commercial_partner_id", "name"], EMPRESAS):
                for e in re.findall(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", (p.get("email") or "").lower()):
                    por_mail.setdefault(e, set()).add(p["commercial_partner_id"][0])
        for i in range(0, len(doms), 100):
            q = ["|"] * (len(doms[i:i + 100]) - 1) + [("email", "=ilike", f"%@{d}") for d in doms[i:i + 100]]
            if not doms[i:i + 100]:
                continue
            for p in odoo.search_read("res.partner", q, ["email", "commercial_partner_id"], EMPRESAS):
                for e in re.findall(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", (p.get("email") or "").lower()):
                    por_dom.setdefault(dominio(e), set()).add(p["commercial_partner_id"][0])
        nombres = {}
        for s in sos:
            if s.get("partner_id"):
                nombres.setdefault(s["com"], s["partner_id"][1])
        por_ref = {}
        for s in sos:
            for r in re.findall(r"[A-Z]{0,4}\d{5,}", (s.get("client_order_ref") or "").upper()):
                por_ref.setdefault(r, []).append(s)
            por_ref.setdefault(s["name"].upper(), []).append(s)

        ahora = time.time()
        items, usados = [], set()

        def so_info(s):
            return {"id": s["id"], "ref": s["name"], "estado": s["state"], "entrega": s.get("delivery_status") or "", "factura": s.get("invoice_status") or "",
                    "total": s.get("amount_total") or 0, "base": s.get("amount_untaxed") or 0, "cid": s["company_id"][0],
                    "empresa": {1: "DISTRIPLUS", 4: "LOGIPAPER", 5: "INGRAF"}.get(s["company_id"][0], ""), "fecha": (s.get("date_order") or "")[:10],
                    "cliente": s["partner_id"][1] if s.get("partner_id") else "", "su_ref": s.get("client_order_ref") or ""}

        def tipo_so(s, ult):
            if s["state"] in ("draft", "sent"):
                return "B" if ahora - max(ult, s["ts"]) > 7 * 86400 else None
            if s["state"] == "sale":
                if s.get("delivery_status") in ("pending", "partial", "started") or not s.get("delivery_status"):
                    return "D" if s.get("delivery_status") else ("E" if s.get("invoice_status") == "to invoice" else None)
                if s.get("invoice_status") == "to invoice":
                    return "E"
            return None

        for hk, g in grupos.items():
            g.sort(key=lambda f: f["ts"])
            pide = [f for f in g if not f["enviado"] and f["clase"] in PIDE and f["ts"] >= t0]
            if not pide:
                continue
            p0 = pide[0]
            ext = [f for f in g if not f["enviado"] and f["de"] not in mias]
            mails = {f["de"] for f in ext}
            for f in g:
                for e in re.findall(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", f["texto"] or "")[:6]:
                    if dominio(e) not in PROPIOS:
                        mails.add(e.lower())
            coms = set()
            for e in mails:
                coms |= por_mail.get(e, set())
            if not coms:
                for e in mails:
                    coms |= por_dom.get(dominio(e), set())
            # pedido de Odoo que corresponde
            cand = []
            refs = set()
            for f in g:
                for r in re.findall(r"[A-Z]{0,4}\d{5,}|S\d{5}|N3\d/\d+", ((f["ref"] or "") + " " + (f["asunto"] or "")).upper()):
                    refs.add(r)
            for r in refs:
                cand += [s for s in por_ref.get(r, []) if s["id"] not in usados]
            if not cand:
                for cm in coms:
                    cand += [s for s in por_com.get(cm, []) if p0["ts"] - 3 * 86400 <= s["ts"] <= p0["ts"] + 45 * 86400 and s["id"] not in usados]
            cand.sort(key=lambda s: (abs(s["ts"] - p0["ts"]), s["ts"]))
            s = cand[0] if cand else None
            ult = max(f["ts"] for f in g)
            contestado = any(f["enviado"] and f["ts"] > p0["ts"] for f in g)
            base = {"hilo": hk, "fecha": dt.datetime.fromtimestamp(p0["ts"]).strftime("%Y-%m-%d"), "asunto": p0["asunto"], "que": p0["que"] or "",
                    "de": p0["de"], "nombre": p0["nombre"], "cuenta": p0["cuenta"], "clase": p0["clase"], "n": len(g), "contestado": contestado,
                    "ultimo": dt.datetime.fromtimestamp(ult).strftime("%Y-%m-%d"), "cli": p0["cli"] or "",
                    "cliente": nombres.get(next(iter(coms))) if len(coms) == 1 and next(iter(coms)) in nombres else (p0["cli"] or p0["nombre"]),
                    "dias": int((ahora - p0["ts"]) // 86400)}
            if s:
                usados.add(s["id"])
                t = tipo_so(s, ult)
                if not t:
                    continue
                items.append({**base, "tipo": t, "clave": f"so:{s['id']}", "so": so_info(s), "cliente": s["partner_id"][1] if s.get("partner_id") else base["cliente"]})
            else:
                if any(f["clase"] == "pedido" or f["clase"] == "acepta" for f in pide):
                    t = "C"
                else:
                    t = "A"
                items.append({**base, "tipo": t, "clave": f"h:{hk}", "so": None})
        # lo de Odoo que no ha salido por correo
        for s in sos:
            if s["id"] in usados or s["ts"] < t0:
                continue
            t = tipo_so(s, dt.datetime.fromisoformat((s.get("write_date") or s["date_order"]).replace(" ", "T")).timestamp())
            if not t:
                continue
            items.append({"tipo": t, "clave": f"so:{s['id']}", "so": so_info(s), "hilo": None, "fecha": s["date_order"][:10], "asunto": "", "que": "",
                          "de": "", "nombre": "", "cuenta": "", "clase": "", "n": 0, "contestado": False, "ultimo": "", "cli": "",
                          "cliente": s["partner_id"][1] if s.get("partner_id") else "", "dias": int((ahora - s["ts"]) // 86400), "sin_correo": True})
        return {"items": items, "calculado": ahora, "desde": desde, "correos": len(filas)}

    def con_marcas(items):
        with con() as c:
            marcas = {r["clave"]: dict(r) for r in c.execute("SELECT * FROM marcas")}
        hoy = dt.date.today().isoformat()
        vivos, avisos = [], []
        for it in items:
            m = marcas.get(it["clave"])
            if m:
                it = {**it, "marca": m}
                if m["estado"] in ("hecho", "descartado"):
                    continue
                if m["estado"] == "recordar" and m.get("recordar"):
                    if m["recordar"] > hoy:
                        it["dormido"] = True
                    else:
                        avisos.append(it)
            vivos.append(it)
        return vivos, avisos

    # ---------------- API ----------------
    def solo_admin(request):
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "Solo el administrador")
        return u

    @app.get("/api/seg")
    def seg(request: Request, recalcular: int = 0):
        solo_admin(request)
        if recalcular or not CACHE["datos"]:
            if not CACHE["calculando"]:
                threading.Thread(target=calcular, daemon=True).start()
                time.sleep(0.3)
        d = CACHE["datos"] or {"items": [], "calculado": 0, "desde": ajuste("desde") or "", "correos": 0}
        vivos, avisos = con_marcas(d["items"])
        with con() as c:
            n = c.execute("SELECT COUNT(*) n, SUM(clase IS NULL AND enviado=0 AND lista=0) sin FROM hist").fetchone()
        return {"items": vivos, "avisos": avisos, "calculado": d["calculado"], "calculando": CACHE["calculando"], "error": CACHE["error"],
                "desde": ajuste("desde") or "", "correos": n["n"] or 0, "sin_clasificar": n["sin"] or 0, "cuentas": [c["usuario"] for c in leer_cuentas()],
                "lectura": {k: EST[k] for k in ("leyendo", "fase", "cuentas", "clasificados", "por_clasificar", "error", "inicio", "fin")}, "tipos": TIPOS}

    @app.get("/api/seg/aviso")
    def seg_aviso(request: Request):
        u = current_user(request)
        if not es_admin(u) or not CACHE["datos"]:
            return {"n": 0}
        vivos, avisos = con_marcas(CACHE["datos"]["items"])
        nuevos = sum(1 for it in vivos if it["tipo"] in ("A", "C") and not it.get("marca") and not it.get("contestado"))
        return {"n": len(avisos) + nuevos, "avisos": len(avisos), "nuevos": nuevos}

    @app.post("/api/seg/leer")
    async def seg_leer(request: Request):
        solo_admin(request)
        b = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
        desde = (b.get("desde") or ajuste("desde") or "2026-01-01")[:10]
        dt.date.fromisoformat(desde)
        if not leer_cuentas():
            raise HTTPException(400, "Primero hay que poner las cuentas en correo_cuentas.txt (carpeta de datos del panel en el NAS): correo;contraseña;servidor IMAP")
        ajuste("desde", desde)
        if not EST["leyendo"]:
            threading.Thread(target=proceso, args=(dt.date.fromisoformat(desde),), daemon=True).start()
        return {"ok": True, "desde": desde}

    @app.post("/api/seg/marca")
    async def seg_marca(request: Request):
        u = solo_admin(request)
        b = await request.json()
        claves = b.get("claves") or [b.get("clave")]
        est = b.get("estado")
        if est not in ("hecho", "descartado", "recordar", "abierto"):
            raise HTTPException(400, "Estado no válido")
        with lock, con() as c:
            for k in claves:
                if not k:
                    continue
                if est == "abierto":
                    c.execute("DELETE FROM marcas WHERE clave=?", (k,))
                else:
                    c.execute("INSERT OR REPLACE INTO marcas VALUES (?,?,?,?,?,?)", (k, est, (b.get("recordar") or "")[:10], (b.get("nota") or "")[:500], u, time.time()))
        return {"ok": True, "n": len(claves)}

    @app.get("/api/seg/hilo/{hk}")
    def seg_hilo(hk: str, request: Request):
        solo_admin(request)
        with con() as c:
            filas = c.execute("SELECT id,cuenta,enviado,msgid,inreply,refs,de,nombre,para,asunto,ts,clase,que,texto,adjuntos FROM hist WHERE ts>=?",
                              (dt.datetime.fromisoformat(ajuste("desde") or "2026-01-01").timestamp() - 20 * 86400,)).fetchall()
        H = hilos(filas)
        g = sorted([f for f in filas if H.get(f["id"]) == hk], key=lambda f: f["ts"])
        return [{"enviado": bool(f["enviado"]), "de": f["de"], "nombre": f["nombre"], "para": f["para"], "asunto": f["asunto"],
                 "fecha": dt.datetime.fromtimestamp(f["ts"]).strftime("%d/%m/%Y %H:%M"), "clase": f["clase"], "que": f["que"],
                 "texto": (f["texto"] or "")[:3000], "adjuntos": json.loads(f["adjuntos"] or "[]"), "cuenta": f["cuenta"]} for f in g]

    # ---------------- cuentas de correo desde el panel (sin tocar el NAS) ----------------
    def mx_de(dom):
        try:
            r = httpx.get("https://dns.google/resolve", params={"name": dom, "type": "MX"}, timeout=10).json()
            return " ".join(a.get("data", "") for a in r.get("Answer", [])).lower()
        except Exception:
            return ""

    def servidores_para(correo):
        dom = dominio(correo)
        mx = mx_de(dom)
        if "ionos" in mx or "1and1" in mx or "kundenserver" in mx:
            return ["imap.ionos.es"], "IONOS"
        if "securemail.pro" in mx or "nominalia" in mx:
            return ["imap.securemail.pro", "pop.securemail.pro"], "Nominalia"
        if "google" in mx:
            return ["imap.gmail.com"], "Google"
        if "outlook" in mx or "office365" in mx:
            return ["outlook.office365.com"], "Microsoft"
        return [f"imap.{dom}", f"mail.{dom}"], mx.split()[-1].rstrip(".") if mx else "desconocido"

    def probar(correo, clave, servidor):
        M = imaplib.IMAP4_SSL(servidor, 993, timeout=25)
        try:
            M.login(correo, clave)
            typ, d = M.select("INBOX", readonly=True)
            return int((d or [b"0"])[0] or 0)
        finally:
            try:
                M.logout()
            except Exception:
                pass

    def escribir_cuenta(correo, clave, servidor, quitar=False):
        lineas = cuentas_f.read_text(encoding="utf-8", errors="ignore").splitlines() if cuentas_f.exists() else [
            "# Cuentas de correo que lee el Panel Folder (una por línea). Formato:", "# correo;contraseña;servidor IMAP"]
        lineas = [l for l in lineas if not (l.strip() and not l.strip().startswith("#") and l.split(";")[0].strip().lower() == correo)]
        if not quitar:
            lineas.append(f"{correo};{clave};{servidor}")
        cuentas_f.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        try:
            os.chmod(cuentas_f, 0o600)
        except Exception:
            pass

    @app.get("/api/seg/cuentas")
    def seg_cuentas(request: Request):
        solo_admin(request)
        return [{"correo": c["usuario"], "servidor": c["servidor"]} for c in leer_cuentas()]

    @app.post("/api/seg/cuentas")
    async def seg_cuenta_nueva(request: Request):
        """Juan escribe la contraseña en el panel; se prueba contra el servidor y se guarda en correo_cuentas.txt del NAS. Nunca se devuelve."""
        solo_admin(request)
        b = await request.json()
        correo = (b.get("correo") or "").strip().lower()
        clave = b.get("clave") or ""
        if not re.fullmatch(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", correo) or not clave or ";" in clave or "\n" in clave:
            raise HTTPException(400, "Revisa el correo y la contraseña (la contraseña no puede llevar punto y coma)")
        lista = [b["servidor"].strip()] if (b.get("servidor") or "").strip() else servidores_para(correo)[0]
        proveedor = "" if (b.get("servidor") or "").strip() else servidores_para(correo)[1]
        errores = []
        for srv in lista:
            try:
                n = probar(correo, clave, srv)
            except imaplib.IMAP4.error as e:
                errores.append(f"{srv}: la contraseña no entra ({str(e)[:80]})")
                continue
            except Exception as e:
                errores.append(f"{srv}: no responde ({str(e)[:60]})")
                continue
            escribir_cuenta(correo, clave, srv)
            return {"ok": True, "correo": correo, "servidor": srv, "proveedor": proveedor, "bandeja": n}
        raise HTTPException(400, f"No se ha podido entrar ({proveedor or 'servidor indicado'}). " + " · ".join(errores))

    @app.post("/api/seg/cuentas/quitar")
    async def seg_cuenta_quitar(request: Request):
        solo_admin(request)
        b = await request.json()
        correo = (b.get("correo") or "").strip().lower()
        escribir_cuenta(correo, "", "", quitar=True)
        return {"ok": True}
