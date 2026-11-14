"""Panel Folder: base + módulo Ventas (solo lectura en esta versión)."""
import datetime as dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .odoo import Odoo, DemoOdoo, OdooError

BASE = Path(__file__).parent
DATA = Path(os.environ.get("PANEL_DATA", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
USERS_FILE = DATA / "usuarios.json"
SECRET_FILE = DATA / "secreto.txt"
if not SECRET_FILE.exists():
    SECRET_FILE.write_text(secrets.token_hex(32))
ENV_USERS = [u.strip().lower() for u in os.environ.get("PANEL_USERS", "juan,almacen").split(",") if u.strip()]
EXTRA_FILE = DATA / "usuarios_extra.json"   # usuarios añadidos desde el panel (p. ej. Mayte)


def extra_users():
    try:
        return json.loads(EXTRA_FILE.read_text())
    except Exception:
        return {}


# Perfiles fijos del panel. "administrador" lo usan Juan y Mayte (hereda la contraseña del antiguo usuario "juan").
ADMIN = "administrador"
PERFILES = {
    ADMIN: {"empresas": ["*"], "modulos": None},
    "contabilidad": {"empresas": ["*"], "modulos": None},   # ve lo mismo que el administrador
    "almacen": {"empresas": ["DISTRIPLUS"], "modulos": ["ventas", "ac", "tablero", "etiquetas"], "sin_facturas": True},
    "compras": {"empresas": ["DISTRIPLUS", "LOGIPAPER"], "modulos": ["ventas", "ac", "tablero"], "sin_facturas": True},
    "tienda1": {"empresas": ["DISTRIPLUS"], "modulos": ["tienda", "etiquetas"], "tienda": "T1", "nombre": "TIENDA 1"},
    "tienda2": {"empresas": ["DISTRIPLUS"], "modulos": ["tienda", "etiquetas"], "tienda": "T2", "nombre": "TIENDA 2"},
    "tiendalogipaper": {"empresas": ["LOGIPAPER"], "modulos": ["tienda", "etiquetas"], "tienda": "T3", "nombre": "TIENDA LOGIPAPER"},
}


def tienda_de(u):
    return PERFILES.get(u, {}).get("tienda")


def nombre_usuario(u):
    return PERFILES.get(u, {}).get("nombre") or u.upper()
ANTIGUOS = {"juan": ADMIN}   # sesiones abiertas con el nombre viejo


def allowed_users():
    return list(PERFILES) + [u for u in extra_users() if u not in PERFILES and u not in ANTIGUOS and u not in ENV_USERS]


def sin_facturas(u):
    return bool(PERFILES.get(u, {}).get("sin_facturas"))


TODOS_MODULOS = ["ventas", "web", "whatsapp", "presupuestos", "ac", "compras", "facturacion", "correo", "vencimientos", "etiquetas", "tablero", "tienda"]


def modulos_de(u):
    if u in PERFILES:
        return list(PERFILES[u]["modulos"] or TODOS_MODULOS)
    mods = list(extra_users().get(u, {}).get("modulos") or TODOS_MODULOS)
    if "compras" in mods and "facturacion" not in mods:  # usuarios creados antes de separar Facturación (Mayte)
        mods.append("facturacion")
    if "compras" in mods and "vencimientos" not in mods:
        mods.append("vencimientos")
    if ("ac" in mods or "compras" in mods) and "etiquetas" not in mods:
        mods.append("etiquetas")
    if ("ac" in mods or "compras" in mods) and "tablero" not in mods:
        mods.append("tablero")
    return mods
DEMO = os.environ.get("PANEL_DEMO") == "1"
odoo = DemoOdoo() if DEMO else Odoo()
ODOO_URL = os.environ.get("ODOO_URL", "").rstrip("/")

app = FastAPI(title="Panel Folder", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_FILE.read_text(), max_age=60 * 60 * 24 * 30, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.middleware("http")
async def sin_cache_vieja(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/presupuestos":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------- usuarios ----------
def load_users():
    return json.loads(USERS_FILE.read_text()) if USERS_FILE.exists() else {}


def save_users(u):
    USERS_FILE.write_text(json.dumps(u, indent=1))


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def usuario_real(request: Request):
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "No has entrado")
    if u in ANTIGUOS:
        u = ANTIGUOS[u]
        request.session["user"] = u
    return u


def current_user(request: Request):
    """El usuario con el que se ve el panel. El administrador puede «ver como» otro usuario para probar."""
    u = usuario_real(request)
    vc = request.session.get("ver_como")
    if vc and u == ADMIN and vc in allowed_users():
        return vc
    return u


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/entrar")
    html = (BASE / "static" / "index.html").read_text()
    html = re.sub(r'(/static/[\w.-]+\.(?:js|css))"', lambda m: f'{m.group(1)}?v={VERSION}"', html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/entrar", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    html = (BASE / "static" / "login.html").read_text()
    opts = "".join(f'<option value="{u}">{nombre_usuario(u)}</option>' for u in allowed_users())
    users = load_users()
    nuevos = [u for u in allowed_users() if u not in users]
    aviso = ""
    if nuevos:
        aviso = "Primera vez para: " + ", ".join(n.capitalize() for n in nuevos) + ". La contraseña que escribas quedará guardada como la tuya."
    import html as h
    return html.replace("{{opts}}", opts).replace("{{error}}", h.escape(error)).replace("{{aviso}}", h.escape(aviso))


@app.post("/entrar")
def login(request: Request, usuario: str = Form(...), clave: str = Form(...)):
    usuario = ANTIGUOS.get(usuario.strip().lower(), usuario.strip().lower())
    if usuario not in allowed_users():
        return RedirectResponse("/entrar?error=Usuario+no+válido", 303)
    users = load_users()
    for viejo, nuevo in ANTIGUOS.items():   # el administrador entra con la contraseña que tenía "juan"
        if nuevo == usuario and usuario not in users and viejo in users:
            users[usuario] = users[viejo]
            save_users(users)
    if usuario not in users:
        if len(clave) < 6:
            return RedirectResponse("/entrar?error=La+contraseña+debe+tener+al+menos+6+caracteres", 303)
        salt = secrets.token_hex(16)
        users[usuario] = {"salt": salt, "hash": hash_pw(clave, salt)}
        save_users(users)
    elif not secrets.compare_digest(users[usuario]["hash"], hash_pw(clave, users[usuario]["salt"])):
        return RedirectResponse("/entrar?error=Contraseña+incorrecta", 303)
    request.session["user"] = usuario
    return RedirectResponse("/", 303)


@app.get("/salir")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/entrar")


# ---------- API común ----------
_COMP = {"t": 0, "v": None}


def companies():
    # las empresas casi nunca cambian: se guardan 10 minutos para no preguntar a Odoo en cada clic
    import time as _t
    if not _COMP["v"] or _t.time() - _COMP["t"] > 600:
        _COMP["v"] = odoo.search_read("res.company", [], ["name"], order="id")
        _COMP["t"] = _t.time()
    return _COMP["v"]


# Empresas que ve cada usuario: PANEL_USER_COMPANIES="almacen=DISTRIPLUS;juan=*"
USER_COMPANIES = {}
for part in os.environ.get("PANEL_USER_COMPANIES", "almacen=DISTRIPLUS").split(";"):
    if "=" in part:
        u, v = part.split("=", 1)
        USER_COMPANIES[u.strip().lower()] = [x.strip().upper() for x in v.split(",") if x.strip()]


def regla_empresas(user):
    if user in PERFILES:
        return list(PERFILES[user]["empresas"])
    if user in extra_users():
        return [x.upper() for x in extra_users()[user].get("empresas", ["*"])]
    return USER_COMPANIES.get(user, ["*"])


def allowed_companies(user):
    rule = regla_empresas(user)
    comps = companies()
    if "*" in rule:
        return comps
    return [c for c in comps if any(k in c["name"].upper() for k in rule)]


def scope(user, company):
    """Devuelve (ids para el contexto de Odoo, dominio por empresa) respetando lo que el usuario puede ver."""
    allowed = [c["id"] for c in allowed_companies(user)]
    if not allowed:
        raise HTTPException(403, "Este usuario no tiene empresas asignadas")
    restricted = len(allowed) < len(companies())
    if company and company != "all" and int(company) in allowed:
        cid = int(company)
        return [cid] + [i for i in allowed if i != cid], [("company_id", "=", cid)]
    if restricted:
        return allowed, [("company_id", "in", allowed)]
    return allowed, []


def guard(fn):
    try:
        return fn()
    except HTTPException:
        raise
    except OdooError as e:
        raise HTTPException(502, f"Odoo respondió con un error: {e}")
    except Exception as e:  # red, etc.
        raise HTTPException(502, f"No se pudo hablar con Odoo: {e}")


VERSION = (BASE / "VERSION").read_text().strip() if (BASE / "VERSION").exists() else "?"


def es_admin(u):
    return u == ADMIN


@app.get("/api/yo")
def yo(request: Request):
    u = current_user(request)
    real = usuario_real(request)
    return {"usuario": u, "nombre": nombre_usuario(u), "demo": DEMO, "odoo_url": ODOO_URL, "version": VERSION,
            "puede_actualizar": es_admin(u) and bool(os.environ.get("PANEL_REPO")), "admin": es_admin(u),
            "modulos": modulos_de(u), "viendo_como": u != real, "real": real, "tienda": tienda_de(u)}


@app.post("/api/ver_como")
async def ver_como(request: Request):
    """Solo el administrador: ver el panel como otro usuario (para probarlo). {usuario: ""} vuelve a administrador."""
    if usuario_real(request) != ADMIN:
        raise HTTPException(403, "Solo el administrador")
    b = await request.json()
    v = (b.get("usuario") or "").strip().lower()
    if v and v not in allowed_users():
        raise HTTPException(400, "Usuario no válido")
    if v and v != ADMIN:
        request.session["ver_como"] = v
    else:
        request.session.pop("ver_como", None)
    return {"ok": True}


@app.post("/api/mi_clave")
async def mi_clave(request: Request):
    """Cambiar la contraseña del usuario con el que se ha entrado (el real, no el de «ver como»)."""
    u = usuario_real(request)
    b = await request.json()
    n = b.get("nueva") or ""
    if len(n) < 6:
        raise HTTPException(400, "La contraseña debe tener al menos 6 caracteres")
    if n != (b.get("repetir") or ""):
        raise HTTPException(400, "Las dos contraseñas no coinciden")
    users = load_users()
    salt = secrets.token_hex(16)
    users[u] = {"salt": salt, "hash": hash_pw(n, salt)}
    save_users(users)
    return {"ok": True}


@app.post("/api/usuarios/borrar_clave")
async def borrar_clave(request: Request):
    """El administrador borra la contraseña de un usuario: la vuelve a elegir el primero que entre con él."""
    if usuario_real(request) != ADMIN:
        raise HTTPException(403, "Solo el administrador")
    b = await request.json()
    v = (b.get("usuario") or "").strip().lower()
    if v == ADMIN:
        raise HTTPException(400, "Para el administrador usa «Cambiar mi contraseña»")
    users = load_users()
    users.pop(v, None)
    save_users(users)
    return {"ok": True}


@app.get("/api/usuarios")
def api_usuarios(request: Request):
    if usuario_real(request) != ADMIN:
        raise HTTPException(403, "Solo el administrador")
    reg = load_users()
    return [{"usuario": u, "nombre": nombre_usuario(u), "extra": u in extra_users(), "empresas": regla_empresas(u), "modulos": modulos_de(u),
             "tiene_clave": u in reg} for u in allowed_users()]


@app.post("/api/usuarios")
async def api_usuario_nuevo(request: Request):
    if not es_admin(current_user(request)):
        raise HTTPException(403, "Solo el administrador")
    b = await request.json()
    u = re.sub(r"[^a-z0-9]", "", (b.get("usuario") or "").strip().lower())
    if not u or u in ENV_USERS or u in PERFILES or u in ANTIGUOS:
        raise HTTPException(400, "Nombre de usuario no válido")
    ex = extra_users()
    ex[u] = {"empresas": b.get("empresas") or ["*"], "modulos": [m for m in (b.get("modulos") or TODOS_MODULOS) if m in TODOS_MODULOS]}
    EXTRA_FILE.write_text(json.dumps(ex, indent=1, ensure_ascii=False))
    if b.get("reiniciar_clave"):
        reg = load_users(); reg.pop(u, None); save_users(reg)
    return {"ok": True}


@app.get("/api/claude/estado")
def claude_estado(request: Request):
    """Comprueba la clave de Claude del NAS sin enseñarla: fecha del archivo, si tiene formato de clave y si Anthropic la acepta."""
    u = current_user(request)
    if not es_admin(u):
        raise HTTPException(403)
    import datetime as _dt
    import httpx as _hx
    f = DATA / "claude_clave.txt"
    if not f.exists():
        return {"archivo": False, "mensaje": "No existe datos/claude_clave.txt en el NAS"}
    txt = f.read_text(errors="ignore")
    ks = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", txt)
    out = {"archivo": True, "modificado": _dt.datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m/%Y %H:%M"),
           "claves_en_archivo": len(ks), "longitud": len(ks[0]) if ks else 0, "otros_caracteres": len(txt.strip()) - (len(ks[0]) if ks else 0)}
    if not ks:
        out["mensaje"] = "El archivo no contiene ninguna clave que empiece por sk-ant-"
        return out
    try:
        r = _hx.get(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/models", timeout=20,
                    headers={"x-api-key": ks[0], "anthropic-version": "2023-06-01"})
        out["anthropic"] = r.status_code
        out["mensaje"] = "La clave funciona" if r.status_code == 200 else f"Anthropic la rechaza ({r.status_code}): {r.text[:150]}"
    except Exception as e:
        out["mensaje"] = f"No se pudo comprobar: {e}"
    return out


@app.post("/api/claude/clave")
async def claude_guardar_clave(request: Request):
    """Juan pega la clave nueva en el panel: se comprueba con Anthropic y, si vale, se guarda en datos/claude_clave.txt."""
    u = current_user(request)
    if not es_admin(u):
        raise HTTPException(403, "Solo Juan puede cambiar la clave")
    import httpx as _hx
    b = await request.json()
    ks = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", b.get("clave") or "")
    if not ks:
        raise HTTPException(400, "Eso no parece una clave de Claude: tiene que empezar por sk-ant-")
    r = _hx.get(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/models", timeout=20,
                headers={"x-api-key": ks[0], "anthropic-version": "2023-06-01"})
    if r.status_code != 200:
        raise HTTPException(400, f"Anthropic no acepta esa clave ({r.status_code}). Revisa que la has copiado entera. No se ha cambiado nada.")
    f = DATA / "claude_clave.txt"
    if f.exists():
        (DATA / "claude_clave.anterior.txt").write_text(f.read_text(errors="ignore"))
    f.write_text(ks[0] + "\n")
    n = 0
    try:
        n = request.app.state.releer_fallidos()
    except Exception:
        pass
    return {"ok": True, "releyendo": n}


@app.get("/clave-claude", response_class=HTMLResponse)
def pagina_clave(request: Request):
    u = current_user(request)
    if not es_admin(u):
        raise HTTPException(403)
    return """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Clave de Claude</title>
<style>body{font-family:system-ui,Arial;background:#f5f3fa;margin:0;padding:40px 16px;color:#1f2328}.c{max-width:560px;margin:auto;background:#fff;border-radius:14px;padding:24px;box-shadow:0 4px 20px rgba(0,0,0,.08)}
h1{color:#5b3ea8;font-size:20px;margin:0 0 6px}ol{padding-left:20px;line-height:1.6}input{width:100%;box-sizing:border-box;padding:12px;font-size:15px;border:2px solid #cfc6e6;border-radius:10px;margin:8px 0}
button{background:#5b3ea8;color:#fff;border:0;border-radius:10px;padding:12px 18px;font-size:15px;cursor:pointer}#m{margin-top:14px;padding:12px;border-radius:8px;display:none}.ok{background:#e7f6ec;color:#17643a}.mal{background:#fdecec;color:#9b1c1c}a{color:#5b3ea8}</style></head>
<body><div class="c"><h1>🔑 Clave de Claude del panel</h1><p id="est">Comprobando la clave actual…</p>
<ol><li>Abre <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener">console.anthropic.com → API Keys</a> y pulsa <b>Create Key</b> (nombre: Panel Folder NAS).</li>
<li>Copia la clave (empieza por <b>sk-ant-</b>).</li><li>Pégala aquí abajo y pulsa <b>Guardar</b>. El panel la prueba antes de guardarla.</li></ol>
<input id="k" type="password" placeholder="sk-ant-…" autocomplete="off"><button id="g">Guardar y comprobar</button><div id="m"></div>
<p style="margin-top:18px"><a href="/">← Volver al panel</a></p></div>
<script>
const m=document.getElementById('m');
fetch('/api/claude/estado').then(r=>r.json()).then(j=>{document.getElementById('est').innerHTML=(j.anthropic===200?'✅ La clave actual funciona.':'❌ La clave actual no funciona'+(j.modificado?' (guardada el '+j.modificado+')':'')+'.')});
document.getElementById('g').onclick=async()=>{const k=document.getElementById('k').value.trim(); if(!k) return; m.style.display='block'; m.className=''; m.textContent='Comprobando con Anthropic…';
 const r=await fetch('/api/claude/clave',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({clave:k})}); const j=await r.json().catch(()=>({}));
 document.getElementById('k').value='';
 if(r.ok){m.className='ok'; m.textContent='✅ Clave guardada y funcionando.'+(j.releyendo?' Estoy volviendo a leer '+j.releyendo+' documento(s) que habían fallado.':''); document.getElementById('est').textContent='✅ La clave actual funciona.';}
 else {m.className='mal'; m.textContent='❌ '+(j.detail||'No se pudo guardar');}};
</script></body></html>"""


@app.post("/api/actualizar")
def actualizar(request: Request):
    u = current_user(request)
    if not es_admin(u):
        raise HTTPException(403, "Solo Juan puede actualizar el panel")
    import threading
    # el contenedor se cierra y Docker lo vuelve a arrancar; al arrancar descarga la última versión
    threading.Timer(1.0, lambda: os._exit(0)).start()
    return {"ok": True}


@app.get("/api/empresas")
def api_empresas(request: Request):
    u = current_user(request)
    return guard(lambda: allowed_companies(u))


def short_company(name):
    n = (name or "").upper()
    for k in ["DISTRIPLUS", "LOGIPAPER", "INGRAF", "FERMASA 2025", "FERMASA INM"]:
        if k in n:
            return k.title().replace("Inm", "Inmuebles")
    return name


SO_FIELDS = ["name", "partner_id", "date_order", "validity_date", "amount_total", "state",
             "delivery_status", "invoice_status", "company_id", "user_id", "client_order_ref", "website_id"]


def so_card(r, kind):
    return {
        "id": r["id"], "model": "sale.order", "ref": r["name"],
        "cliente": r["partner_id"][1] if r.get("partner_id") else "",
        "fecha": (r.get("date_order") or "")[:10],
        "caduca": r.get("validity_date") or "",
        "total": r.get("amount_total") or 0,
        "empresa": short_company(r["company_id"][1] if r.get("company_id") else ""),
        "cid": r["company_id"][0] if r.get("company_id") else None,
        "estado": kind, "entrega": r.get("delivery_status") or "",
        "su_ref": r.get("client_order_ref") or "",
        "web": bool(r.get("website_id")),
    }


@app.get("/api/ventas")
def api_ventas(request: Request, company: str = "all", q: str = ""):
    u = current_user(request)

    def run():
        cids, cd = scope(u, company)
        cli = [("partner_id", "ilike", q)] if q else []
        today = dt.date.today().isoformat()
        so = odoo.search_read("sale.order", cd + cli + [("state", "in", ["draft", "sent", "sale"])],
                              SO_FIELDS, cids, limit=600, order="date_order desc")
        presu, caduc, pedidos, albaranes = [], [], [], []
        for r in so:
            if r["state"] in ("draft", "sent"):
                if r.get("validity_date") and r["validity_date"] < today:
                    caduc.append(so_card(r, "caducado"))
                else:
                    presu.append(so_card(r, "presupuesto"))
            elif r["state"] == "sale":
                ds, inv = r.get("delivery_status"), r.get("invoice_status")
                if ds == "full":
                    if inv != "invoiced":
                        albaranes.append(so_card(r, "servido"))
                else:
                    pedidos.append(so_card(r, "pedido"))
        inv = odoo.search_read(
            "account.move",
            cd + [("partner_id", "ilike", q)] * bool(q) + [("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                                                          ("payment_state", "in", ["not_paid", "partial"]), ("amount_residual", ">", 0)],
            ["name", "partner_id", "invoice_date", "invoice_date_due", "amount_total", "amount_residual",
             "payment_state", "company_id", "invoice_origin"], cids, limit=300, order="invoice_date desc")
        facturas = [{
            "id": r["id"], "model": "account.move", "ref": r["name"],
            "cliente": r["partner_id"][1] if r.get("partner_id") else "",
            "fecha": r.get("invoice_date") or "", "vence": r.get("invoice_date_due") or "",
            "total": r.get("amount_total") or 0, "pendiente": r.get("amount_residual") or 0,
            "empresa": short_company(r["company_id"][1] if r.get("company_id") else ""),
            "origen": r.get("invoice_origin") or "",
            "cid": r["company_id"][0] if r.get("company_id") else None,
            "vencida": bool(r.get("invoice_date_due") and r["invoice_date_due"] < today),
        } for r in inv]
        out = {"presupuestos": presu, "caducados": caduc, "pedidos": pedidos,
               "albaranes": albaranes, "facturas": facturas}
        out = {k: [c for c in v if c["cid"] in cids] for k, v in out.items()}
        if sin_facturas(u):
            out.pop("facturas", None)   # almacén y compras no ven facturas
        if "*" not in regla_empresas(u):
            # usuarios limitados: no ven ventas entre empresas del grupo
            grupo = ["DISTRIPLUS", "LOGIPAPER", "INGRAF", "FERMASA"]
            out = {k: [c for c in v if not any(g in c["cliente"].upper() for g in grupo)] for k, v in out.items()}
        return out

    return guard(run)


@app.get("/api/ventas/{oid}")
def api_detalle(request: Request, oid: int, company: str = "all"):
    u = current_user(request)

    def run():
        cids, cd = scope(u, company)
        if not odoo.search_read("sale.order", cd + [("id", "=", oid)], ["id"], cids):
            raise HTTPException(404, "No encontrado")
        lines = odoo.search_read("sale.order.line", [("order_id", "=", oid)],
                                 ["product_id", "name", "product_uom_qty", "qty_delivered", "qty_invoiced",
                                  "price_unit", "price_subtotal", "display_type"], cids, order="sequence,id")
        msgs = odoo.search_read("mail.message", [("model", "=", "sale.order"), ("res_id", "=", oid)],
                                ["date", "author_id", "body", "subtype_id", "tracking_value_ids"], cids,
                                order="date desc", limit=60)
        hist = []
        for m in msgs:
            txt = re.sub(r"<[^>]+>", " ", m.get("body") or "").strip()
            txt = re.sub(r"\s+", " ", txt)
            if not txt and m.get("tracking_value_ids"):
                txt = f"{len(m['tracking_value_ids'])} campo(s) cambiado(s)"
            if not txt and m.get("subtype_id"):
                txt = m["subtype_id"][1]
            hist.append({"fecha": (m.get("date") or "")[:16], "quien": m["author_id"][1] if m.get("author_id") else "",
                         "texto": txt})
        return {"lineas": [l for l in lines if not l.get("display_type")], "historial": hist}

    return guard(run)


@app.get("/api/salud")
def salud():
    return {"ok": True, "demo": DEMO}


# ---------- módulo Web ----------
SPAM_RE = re.compile(r"\$|https?://|telegra|xss|<script|crypto|bitcoin|casino|earn|[\u0400-\u04FF]", re.I)


def _lead_fields(desc):
    d = re.sub(r"<[^>]+>", " ", desc or "")
    d = re.sub(r"\s+", " ", d).strip()
    svc = re.search(r"lf_service : (.*?)(?: lf_|$)", d)
    msg = re.search(r"lf_message : (.*)$", d)
    return (svc.group(1).strip() if svc else ""), (msg.group(1).strip() if msg else d[:400])


@app.get("/api/web")
def api_web(request: Request):
    u = current_user(request)

    def run():
        cids, cd = scope(u, "all")
        since = (dt.date.today() - dt.timedelta(days=60)).isoformat()
        pedidos = odoo.search_read("sale.order", cd + [("website_id", "!=", False), ("state", "in", ["sent", "sale"]),
                                                         ("date_order", ">=", since)],
                                   ["name", "partner_id", "date_order", "amount_total", "state", "company_id"],
                                   cids, limit=100, order="date_order desc")
        carritos = odoo.search_read("sale.order", cd + [("website_id", "!=", False), ("state", "=", "draft"),
                                                          ("amount_total", ">", 0),
                                                          ("date_order", ">=", (dt.date.today() - dt.timedelta(days=30)).isoformat())],
                                    ["name", "partner_id", "date_order", "amount_total", "order_line", "company_id"],
                                    cids, limit=100, order="date_order desc")
        line_ids = [i for c in carritos for i in c.get("order_line", [])][:500]
        lines = odoo.search_read("sale.order.line", [("id", "in", line_ids)], ["order_id", "name", "product_uom_qty"], cids) if line_ids else []
        by_order = {}
        for l in lines:
            by_order.setdefault(l["order_id"][0], []).append(f'{l["product_uom_qty"]:g} × {l["name"].splitlines()[0][:60]}')
        leads = odoo.search_read("crm.lead", [("medium_id.name", "=", "Website"),
                                              ("create_date", ">=", (dt.date.today() - dt.timedelta(days=120)).isoformat())],
                                 ["contact_name", "partner_name", "email_from", "phone", "description", "create_date",
                                  "stage_id", "user_id"], cids, limit=200, order="create_date desc")
        sol, spam = [], 0
        for l in leads:
            svc, msg = _lead_fields(l.get("description"))
            if SPAM_RE.search(" ".join([msg, l.get("contact_name") or "", l.get("email_from") or ""])):
                spam += 1
                continue
            sol.append({"id": l["id"], "fecha": (l.get("create_date") or "")[:16], "nombre": l.get("contact_name") or l.get("partner_name") or "",
                        "email": l.get("email_from") or "", "telefono": l.get("phone") or "", "servicio": svc, "mensaje": msg,
                        "etapa": l["stage_id"][1] if l.get("stage_id") else "",
                        "asignado": l["user_id"][1] if l.get("user_id") else ""})
        anon = lambda p: (not p) or "public user" in p[1].lower()
        return {
            "pedidos": [{"id": o["id"], "ref": o["name"], "cliente": o["partner_id"][1] if o.get("partner_id") else "",
                         "fecha": (o.get("date_order") or "")[:10], "total": o.get("amount_total") or 0, "estado": o["state"]}
                        for o in pedidos],
            "carritos": [{"id": o["id"], "ref": o["name"], "cliente": "Visitante sin identificar" if anon(o.get("partner_id")) else o["partner_id"][1],
                          "conocido": not anon(o.get("partner_id")), "fecha": (o.get("date_order") or "")[:16],
                          "total": o.get("amount_total") or 0, "lineas": by_order.get(o["id"], [])} for o in carritos],
            "solicitudes": sol, "spam": spam,
        }

    return guard(run)


# ---------- módulo Presupuestos ----------
from . import presupuestos as _presupuestos  # noqa: E402

_presupuestos.setup(app, data_dir=DATA, static_dir=BASE / "static", odoo=odoo, current_user=current_user,
                    all_company_ids=lambda: [c["id"] for c in companies()])

# ---------- módulo WhatsApp ----------
from . import whatsapp as _whatsapp  # noqa: E402

_whatsapp.setup(app, data_dir=DATA, odoo=odoo, current_user=current_user, es_admin=es_admin, demo=DEMO)

# ---------- módulo Panel AC ----------
from . import panelac as _panelac  # noqa: E402

_panelac.setup(app, odoo=odoo, current_user=current_user, scope=scope, guard=guard, odoo_url=ODOO_URL)

# ---------- módulo Compras ----------
from . import compras as _compras  # noqa: E402

_compras.setup(app, data_dir=DATA, odoo=odoo, current_user=current_user, scope=scope, guard=guard, es_admin=es_admin, demo=DEMO)
_compras.setup_fotos(app, odoo=odoo, current_user=current_user, scope=scope, guard=guard)


@app.get("/api/diag")
def diag(request: Request):
    """Solo Juan: qué archivos hay en datos (nombres y tamaños, nunca el contenido)."""
    if not es_admin(current_user(request)):
        raise HTTPException(403, "Solo el administrador")
    out = []
    for p in sorted(DATA.iterdir()):
        if p.is_file():
            txt = p.read_bytes()[:4000] if p.suffix in (".txt", ".rtf", "") and p.stat().st_size < 200000 else b""
            out.append({"nombre": p.name, "bytes": p.stat().st_size,
                        "parece_clave_claude": b"sk-ant-" in txt, "parece_rtf": txt.startswith(b"{\\rtf")})
    return out


# ---------- asistente general (almacén) ----------
from . import asistente as _asistente  # noqa: E402

_asistente.setup(app, data_dir=DATA, odoo=odoo, current_user=current_user, scope=scope, guard=guard, odoo_url=ODOO_URL)

# ---------- módulo Bancos (extractos para contabilidad) ----------
from . import bancos as _bancos  # noqa: E402

_bancos.setup(app, odoo=odoo, current_user=current_user, scope=scope, guard=guard, odoo_url=ODOO_URL)

# ---------- fichas: fotos y datos desde la web del fabricante ----------
from . import fichas as _fichas  # noqa: E402

_fichas.setup(app, data_dir=DATA, odoo=odoo, current_user=current_user, scope=scope, guard=guard)

# ---------- facturar albaranes y cuentas entre empresas ----------
from . import entre_empresas as _entre  # noqa: E402

_entre.setup(app, data_dir=DATA, odoo=odoo, current_user=current_user, scope=scope, guard=guard)

# ---------- correo integrado ----------
from . import correo as _correo  # noqa: E402

_correo.setup(app, data_dir=DATA, odoo=odoo, current_user=current_user, scope=scope, guard=guard)

# ---------- vencimientos ----------
from . import vencimientos as _venc  # noqa: E402

_venc.setup(app, data_dir=DATA, odoo=odoo, current_user=current_user, scope=scope, guard=guard)

# ---------- etiquetas de tienda ----------
from . import etiquetas as _etq  # noqa: E402

_etq.setup(app, data_dir=DATA, odoo=odoo, current_user=current_user, scope=scope, guard=guard)

# ---------- tiendas ----------
from . import tienda as _tienda  # noqa: E402

_tienda.setup(app, odoo=odoo, current_user=current_user, scope=scope, guard=guard, odoo_url=ODOO_URL, tienda_de=tienda_de, data_dir=DATA)

# ---------- tablero de compras (tipo Trello) ----------
from . import tablero as _tab  # noqa: E402

_tab.setup(app, odoo=odoo, current_user=current_user, scope=scope, guard=guard, odoo_url=ODOO_URL)
