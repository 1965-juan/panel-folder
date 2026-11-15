"""Panel Folder: base + módulo Ventas (solo lectura en esta versión)."""
import datetime as dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import Response, HTMLResponse, RedirectResponse, JSONResponse, FileResponse
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
    if nuevos:   # sin decir qué usuarios: a quien no es administrador no se le enseña más que el nombre
        aviso = "Si es la primera vez que entras con tu usuario, la contraseña que escribas quedará guardada como la tuya."
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
            "modulos": modulos_de(u), "viendo_como": u != real, "real": real, "tienda": tienda_de(u),
            "empresas_ids": [c["id"] for c in allowed_companies(u)]}


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
    u = usuario_real(request)   # funciona aunque el administrador esté «viendo como» otro usuario
    if not es_admin(u):
        raise HTTPException(403, "Solo el administrador puede actualizar el panel")
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
        # pedidos ya cobrados en el TPV («recuperar» el pedido en la tienda): están cerrados aunque Odoo los deje como pedido
        cobrado_tpv = {}
        vivos = [r["id"] for r in so if r["state"] == "sale" and r.get("invoice_status") != "invoiced"]
        if vivos:
            try:
                for l in odoo.search_read("pos.order.line", [("sale_order_origin_id", "in", vivos)], ["sale_order_origin_id", "price_subtotal_incl"], cids, limit=20000):
                    k = l["sale_order_origin_id"][0]
                    cobrado_tpv[k] = cobrado_tpv.get(k, 0) + (l.get("price_subtotal_incl") or 0)
            except Exception:
                cobrado_tpv = {}
        # lo que queda de verdad en cada pedido «servido»: algo sin entregar → vuelve a Pedidos; nada que entregar ni facturar → fuera
        resto = {}
        llenos = [r["id"] for r in so if r["state"] == "sale" and r.get("delivery_status") == "full" and r.get("invoice_status") != "invoiced"]
        if llenos:
            try:
                for l in odoo.search_read("sale.order.line", [("order_id", "in", llenos), ("display_type", "=", False), ("is_downpayment", "=", False)],
                                          ["order_id", "product_uom_qty", "qty_delivered", "qty_to_invoice"], cids, limit=50000):
                    x = resto.setdefault(l["order_id"][0], [0, 0])
                    x[0] += max(0, (l.get("product_uom_qty") or 0) - (l.get("qty_delivered") or 0))
                    x[1] += max(0, l.get("qty_to_invoice") or 0)
            except Exception:
                resto = {}
        for r in so:
            if r["state"] == "sale" and (r.get("invoice_status") == "invoiced" or cobrado_tpv.get(r["id"], 0) >= (r.get("amount_total") or 0) - 0.05):
                continue
            if r["id"] in resto:
                falta, por_fact = resto[r["id"]]
                if falta > 0.001:
                    c = so_card(r, "pedido"); c["entrega"] = "partial"; pedidos.append(c); continue
                if por_fact <= 0.001:
                    continue
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
        # marcas de lo hecho con cada presupuesto: enviado al cliente y reclamado (sin confirmar)
        pids = [c["id"] for c in presu + caduc]
        if pids:
            try:
                msgs = odoo.search_read("mail.message", [("model", "=", "sale.order"), ("res_id", "in", pids), ("message_type", "in", ["email", "comment", "email_outgoing"]),
                                                         ("subject", "!=", False)], ["res_id", "subject", "date"], cids, limit=5000, order="date asc")
            except Exception:
                msgs = []
            marca = {}
            for m in msgs:
                sj = (m.get("subject") or "").lower()
                d = (m.get("date") or "")[:10]
                if "seguimos adelante" in sj or "seguimiento de su presupuesto" in sj:
                    marca.setdefault(m["res_id"], {})["reclamado"] = d
                elif "presupuesto" in sj or "quotation" in sj or "cotizaci" in sj:
                    marca.setdefault(m["res_id"], {})["enviado"] = d
            st = {r["id"]: r["state"] for r in so}
            for c in presu + caduc:
                mk = marca.get(c["id"], {})
                c["enviado"] = mk.get("enviado") or ("sí" if st.get(c["id"]) == "sent" else "")
                c["reclamado"] = mk.get("reclamado", "")
        out = {"presupuestos": presu, "caducados": caduc, "pedidos": pedidos,
               "albaranes": albaranes, "facturas": facturas}
        ocul = _ventas_ocultas()
        out = {k: [c for c in v if c["cid"] in cids and not (c.get("model") == "sale.order" and c["id"] in ocul)] for k, v in out.items()}
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
        pk = odoo.search_read("stock.picking", [("sale_id", "=", oid), ("picking_type_code", "=", "outgoing"), ("state", "!=", "cancel")],
                              ["name", "state"], cids, order="id")
        so = odoo.search_read("sale.order", [("id", "=", oid)], ["partner_id", "state"], cids)[0]
        em = odoo.search_read("res.partner", [("id", "=", so["partner_id"][0])], ["email"], cids) if so.get("partner_id") else []
        return {"lineas": [l for l in lines if not l.get("display_type")], "historial": hist,
                "albaranes": [{"id": x["id"], "nombre": x["name"], "estado": x["state"]} for x in pk],
                "email": (em[0].get("email") if em else "") or "", "estado": so["state"]}

    return guard(run)


AVISO_PREPARACION = "Folder - Aviso pedido en preparación"


PLANTILLAS_PRESU = {"presupuesto": "Folder - Envío de presupuesto", "sin_confirmar": "Folder - Presupuesto sin confirmar"}


@app.get("/ventas/{oid}/vista")
def vista_email(request: Request, oid: int, tipo: str = "presupuesto", company: str = "all"):
    """Cómo le llegaría el email al cliente (sin enviarlo): se monta con la misma plantilla de Odoo."""
    u = current_user(request)
    nombre = {**PLANTILLAS_PRESU, "preparacion": AVISO_PREPARACION}.get(tipo)
    if not nombre:
        raise HTTPException(400, "Tipo de email no válido")

    def run():
        cids, cd = scope(u, company)
        so = odoo.search_read("sale.order", cd + [("id", "=", oid)], ["name", "company_id", "partner_id"], cids)
        if not so:
            raise HTTPException(404, "No encontrado")
        c = so[0]["company_id"][0]
        tpl = odoo.search_read("mail.template", [("model", "=", "sale.order"), ("name", "=", nombre)], ["id", "report_template_ids"], [c], limit=1)
        if not tpl:
            raise HTTPException(400, f"No encuentro la plantilla «{nombre}» en Odoo")
        w = odoo.call("mail.compose.message", "create", [c], vals_list=[{"model": "sale.order", "res_ids": f"[{oid}]", "template_id": tpl[0]["id"],
                                                                         "composition_mode": "comment"}])
        w = w[0] if isinstance(w, list) else w
        m = odoo.search_read("mail.compose.message", [("id", "=", w)], ["subject", "body"], [c])[0]
        em = odoo.search_read("res.partner", [("id", "=", so[0]["partner_id"][0])], ["email"], [c] + [x for x in cids if x != c])
        return m, (em[0].get("email") if em else ""), bool(tpl[0].get("report_template_ids"))
    m, email, pdf = guard(run)
    import html as _h
    cab = (f'<div style="font:14px/1.5 -apple-system,Segoe UI,Arial,sans-serif;background:#f3eef9;border-bottom:1px solid #ddd;padding:12px 20px">'
           f'<b>Vista previa · no se ha enviado</b><br>Para: {_h.escape(email or "(el cliente no tiene email)")}<br>'
           f'Asunto: <b>{_h.escape(m.get("subject") or "")}</b>{"<br>📎 Adjunto: presupuesto en PDF" if pdf else ""}</div>')
    return HTMLResponse(f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                        f'<title>Vista previa del email</title></head><body style="margin:0;background:#fff">{cab}<div style="padding:16px">{m.get("body") or ""}</div></body></html>')


@app.post("/api/ventas/{oid}/email")
async def api_email_presu(request: Request, oid: int, company: str = "all"):
    """{tipo}: 'presupuesto' manda el presupuesto en PDF al cliente; 'sin_confirmar' le pregunta si lo archivamos o necesita cambios."""
    u = current_user(request)
    try:
        b = await request.json()
    except Exception:
        b = {}
    tipo = b.get("tipo") or ""
    if tipo not in PLANTILLAS_PRESU:
        raise HTTPException(400, "Tipo de email no válido")

    def run():
        cids, cd = scope(u, company)
        so = odoo.search_read("sale.order", cd + [("id", "=", oid)], ["name", "state", "company_id", "partner_id"], cids)
        if not so:
            raise HTTPException(404, "No encontrado")
        so = so[0]
        c = so["company_id"][0]
        em = odoo.search_read("res.partner", [("id", "=", so["partner_id"][0])], ["email"], [c] + [x for x in cids if x != c])
        if not em or not em[0].get("email"):
            raise HTTPException(400, "El cliente no tiene email en su ficha")
        tpl = odoo.search_read("mail.template", [("model", "=", "sale.order"), ("name", "=", PLANTILLAS_PRESU[tipo])], ["id"], [c], limit=1)
        if not tpl:
            raise HTTPException(400, f"No encuentro la plantilla «{PLANTILLAS_PRESU[tipo]}» en Odoo")
        odoo.call("mail.template", "send_mail", [c], ids=[tpl[0]["id"]], res_id=oid, force_send=True)
        if tipo == "presupuesto" and so["state"] == "draft":
            try:
                odoo.call("sale.order", "write", [c], ids=[oid], vals={"state": "sent"})
            except Exception:
                pass
        return {"ok": True, "pedido": so["name"], "email": em[0]["email"]}

    return guard(run)


OCULTAS_FILE = DATA / "ventas_ocultas.json"


def _ventas_ocultas():
    try:
        return set(json.loads(OCULTAS_FILE.read_text())) if OCULTAS_FILE.exists() else set()
    except Exception:
        return set()


@app.post("/api/ventas/{oid}/accion")
async def api_venta_accion(request: Request, oid: int, company: str = "all"):
    """{accion}: 'cancelar' (presupuesto o pedido sin servir: se cancela en Odoo, se puede recuperar),
    'cerrar_resto' (pedido servido en parte: se cancela lo que falta por servir y pasa a pendiente de facturar),
    'ocultar' (solo deja de salir en el panel, no toca Odoo)."""
    u = current_user(request)
    try:
        b = await request.json()
    except Exception:
        b = {}
    acc = b.get("accion")
    if acc not in ("cancelar", "cerrar_resto", "ocultar"):
        raise HTTPException(400, "Acción no válida")

    def run():
        cids, cd = scope(u, company)
        so = odoo.search_read("sale.order", cd + [("id", "=", oid)], ["name", "state", "company_id", "delivery_status"], cids)
        if not so:
            raise HTTPException(404, "No encontrado")
        so = so[0]
        c = so["company_id"][0]
        if acc == "ocultar":
            o = _ventas_ocultas(); o.add(oid); OCULTAS_FILE.write_text(json.dumps(sorted(o)))
            return {"ok": True, "mensaje": f"{so['name']} ya no sale en el panel (en Odoo sigue igual)"}
        if acc == "cancelar":
            if so["state"] == "sale" and so.get("delivery_status") in ("partial", "full"):
                raise HTTPException(400, "Este pedido ya tiene material servido: usa «Dar por terminado» para cancelar solo lo que falta")
            odoo.call("sale.order", "action_cancel", [c], ids=[oid], context={"disable_cancel_warning": True})
            return {"ok": True, "mensaje": f"{so['name']} cancelado en Odoo (se puede recuperar desde Odoo si hiciera falta)"}
        # cerrar_resto
        pk = odoo.search_read("stock.picking", [("sale_id", "=", oid), ("state", "not in", ["done", "cancel"])], ["name"], [c])
        if not pk:   # lo pendiente no tiene albarán (se canceló o nunca se generó): solo se quita del panel
            o = _ventas_ocultas(); o.add(oid); OCULTAS_FILE.write_text(json.dumps(sorted(o)))
            return {"ok": True, "mensaje": f"{so['name']}: lo que faltaba no tenía albarán pendiente; lo he quitado del panel"}
        odoo.call("stock.picking", "action_cancel", [c], ids=[p["id"] for p in pk])
        return {"ok": True, "mensaje": f"{so['name']}: cancelado lo pendiente ({', '.join(p['name'] for p in pk)}); lo servido queda para facturar"}

    return guard(run)


@app.post("/api/ventas/{oid}/confirmar")
async def api_confirmar(request: Request, oid: int, company: str = "all"):
    """Pasa un presupuesto a pedido y, si se pide, avisa al cliente por email de que se le está preparando el material."""
    u = current_user(request)
    try:
        b = await request.json()
    except Exception:
        b = {}

    def run():
        cids, cd = scope(u, company)
        so = odoo.search_read("sale.order", cd + [("id", "=", oid)], ["name", "state", "company_id", "partner_id"], cids)
        if not so:
            raise HTTPException(404, "No encontrado")
        so = so[0]
        if so["state"] not in ("draft", "sent"):
            raise HTTPException(400, f"{so['name']} ya no es un presupuesto")
        c = so["company_id"][0]
        odoo.call("sale.order", "action_confirm", [c], ids=[oid])
        aviso = ""
        if b.get("avisar"):
            em = odoo.search_read("res.partner", [("id", "=", so["partner_id"][0])], ["email"], [c] + [x for x in cids if x != c])
            if not em or not em[0].get("email"):
                aviso = "el cliente no tiene email: no se ha enviado el aviso"
            else:
                tpl = odoo.search_read("mail.template", [("model", "=", "sale.order"), ("name", "=", AVISO_PREPARACION)], ["id"], [c], limit=1)
                if not tpl:
                    aviso = "no encuentro la plantilla del aviso en Odoo"
                else:
                    try:
                        odoo.call("mail.template", "send_mail", [c], ids=[tpl[0]["id"]], res_id=oid, force_send=True)
                    except Exception as e:
                        aviso = f"no se pudo enviar el aviso: {str(e)[:150]}"
        return {"ok": True, "pedido": so["name"], "avisado": bool(b.get("avisar")) and not aviso, "aviso": aviso}

    return guard(run)


# ---------- Aviso al administrador: encargos «recuperados» en el TPV con el albarán ya validado ----------
AVISOS_FILE = DATA / "avisos_resueltos.json"


def _avisos_resueltos():
    try:
        return set(json.loads(AVISOS_FILE.read_text())) if AVISOS_FILE.exists() else set()
    except Exception:
        return set()


def _avisos_guardar(ids):
    AVISOS_FILE.write_text(json.dumps(sorted(ids)))


def _solo_admin(request):
    if not es_admin(usuario_real(request)):
        raise HTTPException(403, "Solo para el administrador")


def _dobles(dias=30, pos_id=None):
    T = [1, 2, 3, 4, 5]
    desde = (dt.date.today() - dt.timedelta(days=dias)).isoformat()
    dom = [("sale_order_origin_id", "!=", False)] + ([("order_id", "=", pos_id)] if pos_id else [("order_id.date_order", ">=", desde)])
    pl = odoo.search_read("pos.order.line", dom, ["order_id", "sale_order_origin_id"], T, limit=5000)
    pares = {}
    for l in pl:
        pares.setdefault(l["order_id"][0], set()).add(l["sale_order_origin_id"][0])
    if not pares:
        return []
    so_ids = list({i for v in pares.values() for i in v})
    pk = odoo.search_read("stock.picking", [("sale_id", "in", so_ids), ("picking_type_code", "=", "outgoing"), ("state", "=", "done")],
                          ["name", "sale_id", "location_id", "company_id", "date_done"], T, limit=5000)
    por_so = {}
    for p in pk:
        por_so.setdefault(p["sale_id"][0], []).append(p)
    malos = {po: [so for so in sos if so in por_so] for po, sos in pares.items()}
    malos = {k: v for k, v in malos.items() if v}
    if not malos:
        return []
    pos = {p["id"]: p for p in odoo.search_read("pos.order", [("id", "in", list(malos))], ["name", "date_order", "config_id", "partner_id", "amount_total", "payment_ids"], T)}
    pay_ids = [i for p in pos.values() for i in p.get("payment_ids") or []]
    pays = {x["id"]: x for x in odoo.search_read("pos.payment", [("id", "in", pay_ids)], ["payment_method_id", "amount"], T)} if pay_ids else {}
    sos = {x["id"]: x for x in odoo.search_read("sale.order", [("id", "in", so_ids)], ["name", "partner_id", "warehouse_id", "user_id"], T)}
    out = []
    for po, sids in malos.items():
        p = pos.get(po)
        if not p:
            continue
        out.append({"pos_id": po, "ticket": p["name"], "fecha": (p.get("date_order") or "")[:16], "tpv": (p.get("config_id") or [0, ""])[1],
                    "importe": p.get("amount_total") or 0,
                    "pagos": [{"forma": pays[i]["payment_method_id"][1], "importe": pays[i]["amount"]} for i in p.get("payment_ids") or [] if i in pays],
                    "pedidos": [{"id": s, "nombre": sos.get(s, {}).get("name", ""), "cliente": (sos.get(s, {}).get("partner_id") or [0, ""])[1],
                                 "almacen": (sos.get(s, {}).get("warehouse_id") or [0, ""])[1],
                                 "albaranes": [{"id": x["id"], "nombre": x["name"], "fecha": (x.get("date_done") or "")[:10]} for x in por_so[s]]} for s in sids]})
    out.sort(key=lambda x: x["fecha"], reverse=True)
    return out


@app.get("/api/avisos/tpv_dobles")
def avisos_tpv(request: Request, dias: int = 30):
    _solo_admin(request)
    res = _avisos_resueltos()
    return guard(lambda: [a for a in _dobles(dias) if str(a["pos_id"]) not in res])


@app.post("/api/avisos/tpv_dobles/resolver")
async def avisos_tpv_resolver(request: Request):
    """{pos_id, corregir}: corregir=True devuelve al stock lo que se descontó de más (lo de los albaranes validados); luego deja de avisar."""
    _solo_admin(request)
    b = await request.json()
    pos_id = int(b.get("pos_id") or 0)

    def run():
        msg = "marcado como revisado"
        if b.get("corregir"):
            a = _dobles(pos_id=pos_id)
            if not a:
                raise HTTPException(404, "Ya no encuentro ese caso")
            pk_ids = [al["id"] for pe in a[0]["pedidos"] for al in pe["albaranes"]]
            pks = odoo.search_read("stock.picking", [("id", "in", pk_ids)], ["location_id", "company_id"], [1, 2, 3, 4, 5])
            uds = 0
            for p in pks:
                c, loc = p["company_id"][0], p["location_id"][0]
                mv = odoo.search_read("stock.move", [("picking_id", "=", p["id"]), ("state", "=", "done")], ["product_id", "quantity"], [c])
                tot = {}
                for m in mv:
                    tot[m["product_id"][0]] = tot.get(m["product_id"][0], 0) + (m.get("quantity") or 0)
                alm = {x["id"] for x in odoo.search_read("product.product", [("id", "in", list(tot)), ("is_storable", "=", True)], ["id"], [c])}
                cur = {}
                for q in odoo.search_read("stock.quant", [("product_id", "in", list(alm)), ("location_id", "=", loc), ("lot_id", "=", False),
                                                           ("package_id", "=", False), ("owner_id", "=", False)], ["product_id", "quantity"], [c]):
                    cur[q["product_id"][0]] = cur.get(q["product_id"][0], 0) + q["quantity"]
                ids = []
                for pid in alm:
                    r = odoo.call("stock.quant", "create", [c], vals_list=[{"product_id": pid, "location_id": loc, "inventory_quantity": cur.get(pid, 0) + tot[pid]}],
                                  context={"inventory_mode": True})
                    ids += r if isinstance(r, list) else [r]
                    uds += tot[pid]
                if ids:
                    odoo.call("stock.quant", "action_apply_inventory", [c], ids=list(set(ids)),
                              context={"inventory_mode": True, "inventory_name": f"Regularización: doble descuento TPV {a[0]['ticket']}"})
            msg = f"devueltas {uds:g} unidades al stock"
        r = _avisos_resueltos()
        r.add(str(pos_id))
        _avisos_guardar(r)
        return {"ok": True, "mensaje": msg}
    return guard(run)


# ---------- Web: favoritos («lista de deseos») con lo que compra cada cliente ----------
WEB_ID = 1   # web Folder Castellón (Distriplus)


@app.get("/api/web/clientes")
def web_clientes(request: Request, q: str = ""):
    _solo_admin(request)
    if len(q.strip()) < 2:
        return []
    return guard(lambda: [{"id": p["id"], "nombre": p["display_name"]} for p in odoo.search_read(
        "res.partner", [("is_company", "=", True), "|", ("name", "ilike", q), ("vat", "ilike", q)], ["display_name"], [1, 2, 3, 4, 5], limit=15)])


@app.post("/api/web/favoritos")
async def web_favoritos(request: Request):
    """{partner_id, dias=365, solo_ver}: pone en la lista de deseos de la web (de la empresa y de todos sus contactos con acceso)
    lo que ha comprado ese cliente en los últimos días (pedidos de venta y tickets de TPV, todas las empresas)."""
    _solo_admin(request)
    b = await request.json()
    pid = int(b.get("partner_id") or 0)
    dias = int(b.get("dias") or 365)
    T = [1, 2, 3, 4, 5]

    def run():
        cp = odoo.search_read("res.partner", [("id", "=", pid)], ["commercial_partner_id", "name"], T)
        if not cp:
            raise HTTPException(404, "No encuentro el cliente")
        com = cp[0]["commercial_partner_id"][0]
        desde = (dt.date.today() - dt.timedelta(days=dias)).isoformat()
        veces = {}
        for l in odoo.search_read("sale.order.line", [("order_id.partner_id", "child_of", com), ("order_id.state", "=", "sale"),
                                                      ("order_id.date_order", ">=", desde), ("display_type", "=", False), ("product_id", "!=", False)],
                                  ["product_id", "order_id"], T, limit=50000):
            veces.setdefault(l["product_id"][0], set()).add(("so", l["order_id"][0]))
        try:
            for l in odoo.search_read("pos.order.line", [("order_id.partner_id", "child_of", com), ("order_id.date_order", ">=", desde)],
                                      ["product_id", "order_id"], T, limit=50000):
                veces.setdefault(l["product_id"][0], set()).add(("pos", l["order_id"][0]))
        except Exception:
            pass
        pids = list(veces)
        prods = odoo.search_read("product.product", [("id", "in", pids), ("sale_ok", "=", True), ("type", "!=", "service")],
                                 ["display_name", "lst_price", "product_tmpl_id"], T) if pids else []
        tm = list({p["product_tmpl_id"][0] for p in prods})
        pub = {t["id"] for t in odoo.search_read("product.template", [("id", "in", tm), ("is_published", "=", True)], ["id"], T)} if tm else set()
        prods = [p for p in prods if p["product_tmpl_id"][0] in pub]
        # quién entra en la web de esa empresa: la empresa y sus contactos con usuario
        socios = {com} | {u["partner_id"][0] for u in odoo.search_read("res.users", [("share", "=", True), ("partner_id", "child_of", com)], ["partner_id"], T)}
        tarifa = 16
        try:
            pl = odoo.search_read("res.partner", [("id", "=", com)], ["property_product_pricelist"], [1])
            if pl and pl[0].get("property_product_pricelist"):
                tarifa = pl[0]["property_product_pricelist"][0]
        except Exception:
            pass
        ya = {(w["partner_id"][0], w["product_id"][0]) for w in odoo.search_read("product.wishlist", [("partner_id", "in", list(socios))],
                                                                                 ["partner_id", "product_id"], [1], limit=50000)}
        nuevos = [{"partner_id": s, "product_id": p["id"], "website_id": WEB_ID, "pricelist_id": tarifa, "price": p.get("lst_price") or 0}
                  for s in socios for p in prods if (s, p["id"]) not in ya]
        if nuevos and not b.get("solo_ver"):
            for i in range(0, len(nuevos), 200):
                odoo.call("product.wishlist", "create", [1], vals_list=nuevos[i:i + 200])
        return {"cliente": cp[0]["name"], "comprados": len(pids), "en_web": len(prods), "accesos": len(socios),
                "anadidos": 0 if b.get("solo_ver") else len(nuevos), "articulos": sorted(p["display_name"] for p in prods)[:200]}
    return guard(run)


@app.get("/api/web/clientes_activos")
def web_clientes_activos(request: Request, dias: int = 90):
    """Clientes con acceso a la web: cuándo entran, cuántas veces, qué miran, si tienen carrito a medias y favoritos cargados."""
    _solo_admin(request)
    T = [1, 2, 3, 4, 5]

    def run():
        desde = (dt.datetime.now() - dt.timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")
        us = odoo.search_read("res.users", [("share", "=", True), ("active", "=", True)], ["partner_id", "login", "login_date", "create_date"], T, limit=5000)
        if not us:
            return []
        uids = [u["id"] for u in us]
        logs = {}
        try:
            for l in odoo.search_read("res.users.log", [("create_uid", "in", uids), ("create_date", ">=", desde)], ["create_uid"], T, limit=50000):
                logs[l["create_uid"][0]] = logs.get(l["create_uid"][0], 0) + 1
        except Exception:
            pass
        pids = [u["partner_id"][0] for u in us]
        pinfo = {p["id"]: p for p in odoo.search_read("res.partner", [("id", "in", pids)], ["commercial_partner_id", "name"], T)}
        vis, vistos = {}, {}
        try:
            vv = odoo.search_read("website.visitor", [("partner_id", "in", pids)], ["partner_id", "visit_count", "last_connection_datetime"], T, limit=5000)
            for v in vv:
                vis[v["partner_id"][0]] = v
            if vv:
                for t in odoo.search_read("website.track", [("visitor_id", "in", [v["id"] for v in vv]), ("product_id", "!=", False), ("visit_datetime", ">=", desde)],
                                          ["visitor_id", "product_id"], T, limit=20000, order="visit_datetime desc"):
                    pid = next((v["partner_id"][0] for v in vv if v["id"] == t["visitor_id"][0]), None)
                    if pid:
                        lst = vistos.setdefault(pid, [])
                        nm = t["product_id"][1]
                        if nm not in lst:
                            lst.append(nm)
        except Exception:
            pass
        fav = {}
        try:
            for w in odoo.search_read("product.wishlist", [("partner_id", "in", pids)], ["partner_id"], [1], limit=100000):
                fav[w["partner_id"][0]] = fav.get(w["partner_id"][0], 0) + 1
        except Exception:
            pass
        car = {}
        for o in odoo.search_read("sale.order", [("website_id", "!=", False), ("state", "=", "draft"), ("partner_id", "in", pids), ("amount_total", ">", 0)],
                                  ["partner_id", "amount_total", "name"], T, limit=1000):
            car[o["partner_id"][0]] = {"id": o["id"], "ref": o["name"], "total": o["amount_total"]}
        out = []
        for u in us:
            p = u["partner_id"][0]
            com = pinfo.get(p, {}).get("commercial_partner_id") or [p, u["partner_id"][1]]
            v = vis.get(p, {})
            out.append({"user_id": u["id"], "partner_id": p, "empresa_id": com[0], "empresa": com[1], "contacto": u["partner_id"][1],
                        "alta": (u.get("create_date") or "")[:10], "ultimo": (u.get("login_date") or "")[:16],
                        "entradas": logs.get(u["id"], 0), "visitas": v.get("visit_count") or 0,
                        "ultima_visita": (v.get("last_connection_datetime") or "")[:16], "vistos": vistos.get(p, [])[:8],
                        "favoritos": fav.get(p, 0), "carrito": car.get(p)})
        out.sort(key=lambda x: x["ultimo"] or "", reverse=True)
        return out
    return guard(run)


_cache_web = {}


def _grupo_portal():
    if "portal" not in _cache_web:
        x = odoo.search_read("ir.model.data", [("module", "=", "base"), ("name", "=", "group_portal")], ["res_id"], [1])
        _cache_web["portal"] = x[0]["res_id"]
        f = odoo.call("res.users", "fields_get", [1], attributes=["type"])
        _cache_web["campo_grupos"] = "group_ids" if "group_ids" in f else "groups_id"
    return _cache_web["portal"], _cache_web["campo_grupos"]


def _etiqueta(nombre):
    t = odoo.search_read("res.partner.category", [("name", "=ilike", nombre)], ["id"], [1], limit=1)
    if t:
        return t[0]["id"]
    r = odoo.call("res.partner.category", "create", [1], vals_list=[{"name": nombre}])
    return r[0] if isinstance(r, list) else r


@app.post("/api/web/dar_acceso")
async def web_dar_acceso(request: Request):
    """{items:[{id, email, corregir_email}], etiqueta, prueba}: crea el acceso a la web (usuario portal en Distriplus) SIN mandar el email de Odoo
    (la invitación va aparte, p. ej. por Brevo) y marca al cliente con la etiqueta. Cada cliente entra con «Restablecer contraseña»."""
    _solo_admin(request)
    b = await request.json()
    items = b.get("items") or []
    prueba = bool(b.get("prueba"))
    T = [1, 2, 3, 4, 5]
    rx = re.compile(r"[^@\s;,/]+@[^@\s;,/]+\.[A-Za-z]{2,}")

    def run():
        portal, campo = _grupo_portal()
        tag = _etiqueta(b.get("etiqueta") or "WEB") if not prueba else None
        ids = [int(i["id"]) for i in items]
        ps = {p["id"]: p for p in odoo.search_read("res.partner", [("id", "in", ids)], ["name", "email", "user_ids", "active", "category_id"],
                                                     T, limit=5000)}
        emails = [i["email"].strip().lower() for i in items]
        usados = {u["login"].lower(): u for u in odoo.search_read("res.users", [("login", "in", emails), ("active", "in", [True, False])],
                                                                   ["login", "partner_id"], T, limit=5000)}
        res = []
        for it in items:
            pid, em = int(it["id"]), it["email"].strip().lower()
            p = ps.get(pid)
            r = {"id": pid, "email": em, "cliente": p["name"] if p else "?"}
            if not p:
                r["estado"] = "no existe en Odoo"
            elif not rx.fullmatch(em):
                r["estado"] = "email no válido"
            elif p.get("user_ids"):
                r["estado"] = "ya tenía acceso"
            elif em in usados:
                r["estado"] = f"email ya usado por {usados[em]['partner_id'][1]}"
            else:
                r["estado"] = "acceso creado"
                if not prueba:
                    try:
                        if it.get("corregir_email"):
                            odoo.call("res.partner", "write", T, ids=[pid], vals={"email": em})
                        odoo.call("res.users", "create", [1], vals_list=[{"name": p["name"], "login": em, "partner_id": pid, "company_id": 1,
                                                                          "company_ids": [[6, 0, [1]]], campo: [[6, 0, [portal]]]}],
                                  context={"no_reset_password": True})
                        usados[em] = {"partner_id": [pid, p["name"]]}
                    except Exception as e:
                        r["estado"] = "error: " + str(e)[:160]
            if tag and p and r["estado"] in ("acceso creado", "ya tenía acceso") and tag not in (p.get("category_id") or []):
                try:
                    odoo.call("res.partner", "write", T, ids=[pid], vals={"category_id": [[4, tag]]})
                except Exception:
                    pass
            res.append(r)
        return res
    return guard(run)


@app.post("/api/web/promo")
async def web_promo(request: Request):
    """{porcentaje, hasta, nombre, prueba}: promoción automática en la web de Distriplus (descuento % sobre el pedido) hasta esa fecha."""
    _solo_admin(request)
    b = await request.json()
    pct = float(b.get("porcentaje") or 3)
    hasta = b.get("hasta") or f"{dt.date.today().year}-12-31"
    nombre = b.get("nombre") or f"Web: {pct:g} % extra hasta {hasta[8:10]}/{hasta[5:7]}"

    def run():
        ya = odoo.search_read("loyalty.program", [("name", "=", nombre)], ["id", "active"], [1], limit=1)
        if ya:
            return {"ok": True, "id": ya[0]["id"], "ya_existia": True}
        f = odoo.call("loyalty.program", "fields_get", [1], attributes=["type"])
        vals = {"name": nombre, "program_type": "promotion", "trigger": "auto", "applies_on": "current", "company_id": 1,
                "date_from": dt.date.today().isoformat(), "date_to": hasta,
                "rule_ids": [[0, 0, {"minimum_amount": 0, "minimum_qty": 0, "reward_point_amount": 1}]],
                "reward_ids": [[0, 0, {"reward_type": "discount", "discount": pct, "discount_mode": "percent", "discount_applicability": "order"}]]}
        if "website_id" in f:
            vals["website_id"] = 1
        if "pos_ok" in f:
            vals["pos_ok"] = False
        if "sale_ok" in f:
            vals["sale_ok"] = False      # no en pedidos hechos desde Odoo
        if "ecommerce_ok" in f:
            vals["ecommerce_ok"] = True  # solo pedidos de la web
        if b.get("prueba"):
            return {"ok": True, "vals": {k: v for k, v in vals.items() if k not in ("rule_ids", "reward_ids")}, "campos": [k for k in ("website_id", "pos_ok", "sale_ok", "ecommerce_ok") if k in f]}
        r = odoo.call("loyalty.program", "create", [1], vals_list=[vals])
        return {"ok": True, "id": r[0] if isinstance(r, list) else r}
    return guard(run)


EXCLUIR_WEB = ("PEDIDO ESCOLAR", "CONSUMIDOR", "CLIENTE CONTADO", "CLIENTES VARIOS", "VARIOS", "CLIENTE TPV", "CLIENTE GENERICO", "CLIENTE GENÉRICO")


@app.get("/web/candidatos.csv")
def web_candidatos(request: Request, dias: int = 365):
    """Clientes de Distriplus o Logipaper con compras en el último año, para invitarlos a la web (Excel/CSV)."""
    _solo_admin(request)
    import csv, io
    T = [1, 2, 3, 4, 5]

    def run():
        desde = (dt.date.today() - dt.timedelta(days=dias)).isoformat()
        propias = {c["partner_id"][0] for c in odoo.search_read("res.company", [], ["partner_id"], T)}
        ac = {}

        def suma(pid, emp, imp, fecha):
            a = ac.setdefault(pid, {"emp": set(), "n": 0, "imp": 0.0, "ult": ""})
            a["emp"].add(emp); a["n"] += 1; a["imp"] += imp or 0; a["ult"] = max(a["ult"], fecha or "")
        for o in odoo.search_read("sale.order", [("state", "=", "sale"), ("company_id", "in", [1, 4]), ("date_order", ">=", desde)],
                                  ["partner_id", "company_id", "amount_untaxed", "date_order"], T, limit=50000):
            suma(o["partner_id"][0], o["company_id"][0], o["amount_untaxed"], (o["date_order"] or "")[:10])
        try:
            for o in odoo.search_read("pos.order", [("partner_id", "!=", False), ("company_id", "in", [1, 4]), ("date_order", ">=", desde),
                                                    ("state", "!=", "cancel")], ["partner_id", "company_id", "amount_total", "amount_tax", "date_order"], T, limit=50000):
                suma(o["partner_id"][0], o["company_id"][0], (o["amount_total"] or 0) - (o["amount_tax"] or 0), (o["date_order"] or "")[:10])
        except Exception:
            pass
        ps = {p["id"]: p for p in odoo.search_read("res.partner", [("id", "in", list(ac))], ["commercial_partner_id"], T, limit=50000)} if ac else {}
        com = {}
        for pid, a in ac.items():
            c = (ps.get(pid, {}).get("commercial_partner_id") or [pid])[0]
            x = com.setdefault(c, {"emp": set(), "n": 0, "imp": 0.0, "ult": ""})
            x["emp"] |= a["emp"]; x["n"] += a["n"]; x["imp"] += a["imp"]; x["ult"] = max(x["ult"], a["ult"])
        datos = {p["id"]: p for p in odoo.search_read("res.partner", [("id", "in", list(com))],
                                                      ["name", "vat", "email", "phone", "city", "is_company", "active"], T, limit=50000)} if com else {}
        accesos = {}
        for u in odoo.search_read("res.users", [("share", "=", True)], ["partner_id", "login_date", "login"], T, limit=10000):
            pc = odoo.search_read("res.partner", [("id", "=", u["partner_id"][0])], ["commercial_partner_id"], T)
            if pc:
                accesos.setdefault(pc[0]["commercial_partner_id"][0], []).append(u)
        filas = []
        for c, x in com.items():
            d = datos.get(c)
            if not d or c in propias or any(k in (d["name"] or "").upper() for k in EXCLUIR_WEB):
                continue
            em = (d.get("email") or "").strip()
            ok = bool(re.fullmatch(r"[^@\s;,]+@[^@\s;,]+\.[A-Za-z]{2,}", em))
            us = accesos.get(c, [])
            filas.append([d["name"], d.get("vat") or "", em, "sí" if ok else ("falta" if not em else "revisar"), d.get("phone") or "", d.get("city") or "",
                          " + ".join({1: "Distriplus", 4: "Logipaper"}[e] for e in sorted(x["emp"])), x["n"], round(x["imp"], 2), x["ult"],
                          "sí" if us else "no", max((u.get("login_date") or "")[:10] for u in us) if us else "", c])
        filas.sort(key=lambda f: -f[8])
        return filas
    filas = guard(run)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Cliente", "CIF", "Email", "Email válido", "Teléfono", "Población", "Empresa", "Nº pedidos/tickets", "Importe último año (sin IVA)",
                "Última compra", "Ya tiene acceso web", "Último acceso web", "ID Odoo"])
    for f in filas:
        f = list(f); f[8] = f"{f[8]:.2f}".replace(".", ",")
        w.writerow(f)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="clientes_web_{dt.date.today().isoformat()}.csv"'})


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

_etq.setup(app, data_dir=DATA, odoo=odoo, current_user=current_user, scope=scope, guard=guard, tienda_de=tienda_de)

# ---------- tiendas ----------
from . import tienda as _tienda  # noqa: E402

_tienda.setup(app, odoo=odoo, current_user=current_user, scope=scope, guard=guard, odoo_url=ODOO_URL, tienda_de=tienda_de, data_dir=DATA)

# ---------- tablero de compras (tipo Trello) ----------
from . import tablero as _tab  # noqa: E402

_tab.setup(app, odoo=odoo, current_user=current_user, scope=scope, guard=guard, odoo_url=ODOO_URL, data_dir=DATA)
