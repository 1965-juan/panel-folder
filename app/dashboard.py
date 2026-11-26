"""Dashboard: la situación de las empresas de un vistazo (solo administrador).

- Stock valorado a coste por ubicación (Almacén central, Tienda 1, Tienda 2, Logipaper = tienda + almacén VP…).
- Ventas: lo mismo que el panel de Ventas (pedidos y albaranes por facturar) y las facturas por cobrar.
- Cajas: las de las tiendas (TPV) y la caja general de cada empresa.
- Bancos: cada cuenta por separado, con su saldo en Odoo y la fecha del último movimiento.
- Cuentas entre empresas (clientes/proveedores entre ellas) y cuentas con socios (551/552).
- Facturas a pagar: hueco reservado (todavía sin datos).
Todo se calcula de una vez para las cinco empresas, se guarda 15 minutos y la pantalla filtra por empresa.
Así no se le pide nada a Odoo cada vez que alguien abre la pantalla.
"""
import datetime as dt
import re
import threading
import time

from fastapi import HTTPException, Request

TODAS = [1, 2, 3, 4, 5]
NOMBRES = {1: "Distriplus", 2: "Fermasa 2025", 3: "Fermasa Inmuebles", 4: "Logipaper", 5: "Ingraf"}
GRUPO = ["DISTRIPLUS", "LOGIPAPER", "INGRAF", "FERMASA"]
# ubicaciones de stock: (nombre, empresa, ubicación de Odoo)
STOCK = [("Almacén central", 1, 8), ("Tienda 1", 1, 34), ("Tienda 2", 1, 40), ("Logipaper (tienda + almacén VP)", 4, 46),
         ("Lote provisional", 1, 57), ("Ingraf (muestras)", 5, 52)]
# diarios «banco» que no son cuentas de banco de verdad (tarjeteros, gastos, embargos…)
NO_BANCO = re.compile(r"SERV|GASTOS|TARJET|EMBARGO", re.I)
TTL = 900


def setup(app, *, odoo, current_user, guard, es_admin, ventas_datos):
    cache = {"t": 0, "datos": None, "error": ""}
    lock = threading.Lock()

    def es_grupo(nombre):
        n = (nombre or "").upper()
        return any(g in n for g in GRUPO)

    def rg(model, domain, fields, groupby):
        return odoo.call(model, "read_group", TODAS, domain=domain, fields=fields, groupby=groupby, lazy=False) or []

    # ---------- stock ----------
    def stock():
        out = []
        for nombre, emp, loc in STOCK:
            qs = odoo.search_read("stock.quant", [("location_id", "child_of", loc), ("quantity", ">", 0)], ["product_id", "quantity"], [emp], limit=50000)
            if not qs:
                out.append({"nombre": nombre, "empresa": emp, "valor": 0, "uds": 0, "refs": 0})
                continue
            cant = {}
            for q in qs:
                cant[q["product_id"][0]] = cant.get(q["product_id"][0], 0) + (q["quantity"] or 0)
            coste = {}
            ids = list(cant)
            for i in range(0, len(ids), 4000):
                for p in odoo.search_read("product.product", [("id", "in", ids[i:i + 4000]), ("active", "in", [True, False])], ["standard_price"], [emp], limit=4000):
                    coste[p["id"]] = p.get("standard_price") or 0
            sin = sum(1 for p in ids if not coste.get(p))
            out.append({"nombre": nombre, "empresa": emp, "valor": round(sum(c * coste.get(p, 0) for p, c in cant.items()), 2),
                        "uds": round(sum(cant.values())), "refs": len(ids), "sin_coste": sin})
        return out

    # ---------- ventas (igual que el panel de Ventas) ----------
    def ventas(u):
        v = ventas_datos(u, "all")
        res = {e: {"pedidos": [0, 0.0], "albaranes": [0, 0.0]} for e in TODAS}
        for k in ("pedidos", "albaranes"):
            for c in v.get(k, []):
                if c.get("cid") in res and not es_grupo(c.get("cliente")):
                    res[c["cid"]][k][0] += 1
                    res[c["cid"]][k][1] += c.get("total") or 0
        hoy = dt.date.today().isoformat()
        base = [("move_type", "in", ["out_invoice", "out_refund"]), ("state", "=", "posted"), ("payment_state", "in", ["not_paid", "partial"])]
        fac = {e: {"n": 0, "importe": 0.0, "vencido": 0.0} for e in TODAS}
        for r in odoo.search_read("account.move", base, ["company_id", "partner_id", "amount_residual_signed", "invoice_date_due"], TODAS, limit=20000):
            e = r["company_id"][0] if r.get("company_id") else None
            if e not in fac or es_grupo(r["partner_id"][1] if r.get("partner_id") else ""):
                continue
            imp = r.get("amount_residual_signed") or 0
            fac[e]["n"] += 1
            fac[e]["importe"] += imp
            if r.get("invoice_date_due") and r["invoice_date_due"] < hoy:
                fac[e]["vencido"] += imp
        return [{"empresa": e, "pedidos_n": res[e]["pedidos"][0], "pedidos": round(res[e]["pedidos"][1], 2),
                 "albaranes_n": res[e]["albaranes"][0], "albaranes": round(res[e]["albaranes"][1], 2),
                 "facturas_n": fac[e]["n"], "facturas": round(fac[e]["importe"], 2), "vencido": round(fac[e]["vencido"], 2)} for e in TODAS]

    # ---------- saldos contables por cuenta ----------
    def saldos(cuentas):
        if not cuentas:
            return {}
        g = rg("account.move.line", [("account_id", "in", list(cuentas)), ("parent_state", "=", "posted")], ["balance:sum"], ["account_id", "company_id"])
        return {(x["account_id"][0], x["company_id"][0]): x.get("balance") or 0 for x in g if x.get("account_id") and x.get("company_id")}

    def ultimo_mov(diarios):
        if not diarios:
            return {}
        g = rg("account.bank.statement.line", [("journal_id", "in", list(diarios))], ["date:max"], ["journal_id"])
        return {x["journal_id"][0]: x.get("date") for x in g if x.get("journal_id")}

    # ---------- cajas y bancos ----------
    def tesoreria():
        ds = odoo.search_read("account.journal", [("type", "in", ["bank", "cash"])], ["name", "type", "company_id", "default_account_id", "bank_acc_number"], TODAS, limit=500)
        ds = [d for d in ds if d.get("default_account_id") and d.get("company_id")]
        sal = saldos({d["default_account_id"][0] for d in ds})
        ult = ultimo_mov({d["id"] for d in ds})
        # cajas de las tiendas: los diarios de efectivo que usa algún TPV
        tpv_diario = {}
        try:
            for m in odoo.search_read("pos.payment.method", [("is_cash_count", "=", True)], ["journal_id"], TODAS, limit=100):
                if m.get("journal_id"):
                    tpv_diario[m["journal_id"][0]] = True
            ses = {}
            for c in odoo.search_read("pos.config", [], ["name", "company_id", "current_session_id"], TODAS, limit=50):
                ses[c["id"]] = c
            # último cierre de cada TPV: lo contado en el cajón
            cierres = {}
            for s in odoo.search_read("pos.session", [("state", "=", "closed")], ["config_id", "stop_at", "cash_register_balance_end_real"], TODAS, limit=60, order="stop_at desc"):
                cierres.setdefault(s["config_id"][0], s)
            pm = odoo.search_read("pos.payment.method", [("is_cash_count", "=", True)], ["journal_id", "config_ids"], TODAS, limit=100)
            config_de = {}
            for m in pm:
                for cid in m.get("config_ids") or []:
                    if m.get("journal_id"):
                        config_de[m["journal_id"][0]] = cid
        except Exception:
            ses, cierres, config_de = {}, {}, {}
        bancos, otros, tiendas, generales, vistos = [], [], [], [], set()
        for d in ds:
            clave = (d["default_account_id"][0], d["company_id"][0])
            if clave in vistos:   # dos diarios con la misma cuenta (p. ej. caja general de Distriplus): una sola vez
                continue
            vistos.add(clave)
            x = {"id": d["id"], "nombre": d["name"], "empresa": d["company_id"][0], "saldo": round(sal.get(clave, 0), 2),
                 "iban": d.get("bank_acc_number") or "", "cuenta": d["default_account_id"][1], "ultimo": ult.get(d["id"]) or ""}
            if d["type"] == "bank":
                (otros if NO_BANCO.search(d["name"]) else bancos).append(x)
            elif d["id"] in tpv_diario:
                cid = config_de.get(d["id"])
                c, s = ses.get(cid) or {}, cierres.get(cid) or {}
                x.update({"tpv": c.get("name") or "", "abierta": bool(c.get("current_session_id")),
                          "ultimo_cierre": (s.get("stop_at") or "")[:16], "contado": s.get("cash_register_balance_end_real")})
                tiendas.append(x)
            else:
                generales.append(x)
        return {"bancos": bancos, "bancos_otros": otros, "cajas_tiendas": tiendas, "cajas_generales": generales}

    # ---------- entre empresas y socios ----------
    def entre_empresas():
        cos = odoo.search_read("res.company", [], ["name", "partner_id"], TODAS, limit=20)
        socio = {c["partner_id"][0]: c["id"] for c in cos if c.get("partner_id")}
        g = rg("account.move.line", [("partner_id.commercial_partner_id", "in", list(socio)), ("parent_state", "=", "posted"),
                                     ("account_id.account_type", "in", ["asset_receivable", "liability_payable"])],
               ["balance:sum"], ["company_id", "partner_id"])
        # partner -> commercial partner (por si el apunte está en un contacto de la empresa)
        pids = list({x["partner_id"][0] for x in g if x.get("partner_id")})
        com = {p["id"]: p["commercial_partner_id"][0] for p in odoo.search_read("res.partner", [("id", "in", pids)], ["commercial_partner_id"], TODAS, limit=500)} if pids else {}
        par = {}
        for x in g:
            if not x.get("partner_id") or not x.get("company_id"):
                continue
            otra = socio.get(com.get(x["partner_id"][0], x["partner_id"][0]))
            libro = x["company_id"][0]
            if not otra or otra == libro:
                continue
            par[(libro, otra)] = par.get((libro, otra), 0) + (x.get("balance") or 0)
        cuentas = [{"libro": a, "otra": b, "saldo": round(v, 2)} for (a, b), v in sorted(par.items()) if abs(v) >= 0.5]
        # cuentas con socios y con empresas del grupo (551 / 552)
        acc = odoo.search_read("account.account", ["|", ("code", "=like", "551%"), ("code", "=like", "552%")], ["code", "name"], TODAS, limit=200)
        socios = []
        if acc:
            nombre = {a["id"]: f'{a["code"]} {a["name"]}' for a in acc}
            for x in rg("account.move.line", [("account_id", "in", list(nombre)), ("parent_state", "=", "posted")], ["balance:sum"], ["company_id", "account_id"]):
                if x.get("company_id") and x.get("account_id") and abs(x.get("balance") or 0) >= 0.5:
                    socios.append({"empresa": x["company_id"][0], "cuenta": nombre.get(x["account_id"][0], x["account_id"][1]), "saldo": round(x["balance"], 2)})
        return {"cuentas": cuentas, "socios": socios}

    def calcular(u):
        partes, errores = {}, []
        for k, fn in (("stock", stock), ("ventas", lambda: ventas(u)), ("tesoreria", tesoreria), ("entre", entre_empresas)):
            try:
                partes[k] = fn()
            except Exception as e:   # si falla una parte, el resto se enseña igual
                errores.append(f"{k}: {str(e)[:200]}")
                partes[k] = None
        partes.update({"empresas": NOMBRES, "a_pagar": None, "hecho": time.time(), "errores": errores})
        return partes

    def refrescar(u):
        if not lock.acquire(blocking=False):
            return
        try:
            cache["datos"] = calcular(u)
            cache["t"] = time.time()
            cache["error"] = ""
        except Exception as e:
            cache["error"] = str(e)[:300]
        finally:
            lock.release()

    @app.get("/api/odoo_estado")
    def odoo_estado(request: Request):
        """Cuántas peticiones le hace el panel a Odoo y si Odoo nos tiene frenados (429)."""
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "Solo el administrador")
        return odoo.estado() if hasattr(odoo, "estado") else {}

    @app.post("/api/odoo_pausa")
    def odoo_pausa(request: Request, minutos: int = 60):
        """Solo admin: el panel deja de llamar a Odoo durante N minutos (0 = reanudar)."""
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "Solo el administrador")
        odoo.pausa_hasta = time.time() + max(0, minutos) * 60
        return odoo.estado()

    @app.get("/api/dashboard")
    def dashboard(request: Request, refrescar_ya: int = 0):
        u = current_user(request)
        if not es_admin(u):
            raise HTTPException(403, "El dashboard solo lo ve el administrador")
        viejo = time.time() - cache["t"] > TTL
        if cache["datos"] is None or refrescar_ya:
            guard(lambda: refrescar(u))   # la primera vez (o a petición) se espera a que termine
        elif viejo:
            threading.Thread(target=refrescar, args=(u,), daemon=True).start()   # se enseña lo guardado y se renueva por detrás
        if cache["datos"] is None:
            raise HTTPException(502, cache["error"] or "No se han podido leer los datos de Odoo")
        return {**cache["datos"], "calculando": lock.locked()}
