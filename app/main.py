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
ALLOWED_USERS = [u.strip().lower() for u in os.environ.get("PANEL_USERS", "juan,almacen").split(",") if u.strip()]
DEMO = os.environ.get("PANEL_DEMO") == "1"
odoo = DemoOdoo() if DEMO else Odoo()
ODOO_URL = os.environ.get("ODOO_URL", "").rstrip("/")

app = FastAPI(title="Panel Folder", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_FILE.read_text(), max_age=60 * 60 * 24 * 30, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


# ---------- usuarios ----------
def load_users():
    return json.loads(USERS_FILE.read_text()) if USERS_FILE.exists() else {}


def save_users(u):
    USERS_FILE.write_text(json.dumps(u, indent=1))


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def current_user(request: Request):
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "No has entrado")
    return u


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if not request.session.get("user"):
        return RedirectResponse("/entrar")
    return FileResponse(BASE / "static" / "index.html")


@app.get("/entrar", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    html = (BASE / "static" / "login.html").read_text()
    opts = "".join(f'<option value="{u}">{u.capitalize()}</option>' for u in ALLOWED_USERS)
    users = load_users()
    nuevos = [u for u in ALLOWED_USERS if u not in users]
    aviso = ""
    if nuevos:
        aviso = "Primera vez para: " + ", ".join(n.capitalize() for n in nuevos) + ". La contraseña que escribas quedará guardada como la tuya."
    import html as h
    return html.replace("{{opts}}", opts).replace("{{error}}", h.escape(error)).replace("{{aviso}}", h.escape(aviso))


@app.post("/entrar")
def login(request: Request, usuario: str = Form(...), clave: str = Form(...)):
    usuario = usuario.strip().lower()
    if usuario not in ALLOWED_USERS:
        return RedirectResponse("/entrar?error=Usuario+no+válido", 303)
    users = load_users()
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
def companies():
    return odoo.search_read("res.company", [], ["name"], order="id")


# Empresas que ve cada usuario: PANEL_USER_COMPANIES="almacen=DISTRIPLUS;juan=*"
USER_COMPANIES = {}
for part in os.environ.get("PANEL_USER_COMPANIES", "almacen=DISTRIPLUS").split(";"):
    if "=" in part:
        u, v = part.split("=", 1)
        USER_COMPANIES[u.strip().lower()] = [x.strip().upper() for x in v.split(",") if x.strip()]


def allowed_companies(user):
    rule = USER_COMPANIES.get(user, ["*"])
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
    return "*" in USER_COMPANIES.get(u, ["*"])


@app.get("/api/yo")
def yo(request: Request):
    u = current_user(request)
    return {"usuario": u, "demo": DEMO, "odoo_url": ODOO_URL, "version": VERSION,
            "puede_actualizar": es_admin(u) and bool(os.environ.get("PANEL_REPO"))}


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
             "delivery_status", "invoice_status", "company_id", "user_id", "client_order_ref"]


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
        if "*" not in USER_COMPANIES.get(u, ["*"]):
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
