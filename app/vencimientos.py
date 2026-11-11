"""Vencimientos: lo que hay que pagar (y cobrar) por empresa y fecha.
- De Odoo: apuntes pendientes de facturas contabilizadas (proveedores / clientes) con su fecha de vencimiento.
- A mano: vencimientos que no salen de una factura (préstamos, leasing, alquileres, seguros, impuestos…),
  con repetición mensual si hace falta, y se marcan como pagados.
"""
import datetime as dt
import sqlite3
import threading
from pathlib import Path

from fastapi import Request, HTTPException

TODAS = [1, 2, 3, 4, 5]


def setup(app, *, data_dir: Path, odoo, current_user, scope, guard):
    db = data_dir / "vencimientos.db"
    lock = threading.Lock()

    def con():
        c = sqlite3.connect(db, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("""CREATE TABLE IF NOT EXISTS manuales (id INTEGER PRIMARY KEY AUTOINCREMENT, empresa INTEGER, tipo TEXT, concepto TEXT, tercero TEXT,
                     fecha TEXT, importe REAL, pagado INTEGER DEFAULT 0, nota TEXT, grupo TEXT, usuario TEXT, creado REAL)""")
        c.execute("CREATE TABLE IF NOT EXISTS marcas (aml_id INTEGER PRIMARY KEY, nota TEXT, fecha_prevista TEXT, usuario TEXT)")
        return c

    def ctx(request, company="all"):
        u = current_user(request)
        cids, _ = scope(u, company)
        return u, cids

    @app.get("/api/vencimientos")
    def lista(request: Request, company: str = "all", tipo: str = "pagos", hasta: str = "", incluir_pagados: int = 0):
        u, cids = ctx(request, company)
        hasta = hasta or (dt.date.today() + dt.timedelta(days=120)).isoformat()

        def run():
            cuenta = "liability_payable" if tipo == "pagos" else "asset_receivable"
            ls = odoo.search_read("account.move.line", [("account_type", "=", cuenta), ("parent_state", "=", "posted"), ("reconciled", "=", False),
                                                        ("amount_residual", "!=", 0), ("company_id", "in", cids)],
                                  ["move_id", "partner_id", "date_maturity", "date", "amount_residual", "company_id", "ref", "name"], company_ids=cids, limit=5000)
            mids = list({l["move_id"][0] for l in ls})
            mv = {m["id"]: m for m in odoo.search_read("account.move", [("id", "in", mids)], ["name", "ref", "move_type", "invoice_date", "payment_state",
                                                                                          "preferred_payment_method_line_id" if False else "invoice_payment_term_id"],
                                                         company_ids=cids)} if mids else {}
            with con() as c:
                marcas = {r["aml_id"]: dict(r) for r in c.execute("SELECT * FROM marcas").fetchall()}
            out = []
            for l in ls:
                m = mv.get(l["move_id"][0], {})
                imp = -l["amount_residual"] if tipo == "pagos" else l["amount_residual"]
                f = l.get("date_maturity") or l.get("date")
                mk = marcas.get(l["id"], {})
                out.append({"id": f"o{l['id']}", "aml_id": l["id"], "origen": "odoo", "empresa": l["company_id"][0], "empresa_n": l["company_id"][1].split()[0],
                            "fecha": mk.get("fecha_prevista") or f, "vence": f, "tercero": (l.get("partner_id") or [0, "—"])[1],
                            "concepto": (m.get("name") or l["move_id"][1]) + (f" · {m['ref']}" if m.get("ref") else ""), "importe": round(imp, 2),
                            "move_id": l["move_id"][0], "tipo_doc": m.get("move_type"), "forma": (m.get("invoice_payment_term_id") or [0, ""])[1],
                            "nota": mk.get("nota") or "", "pagado": False})
            with con() as c:
                q = "SELECT * FROM manuales WHERE tipo=? AND empresa IN (%s)" % ",".join("?" * len(cids))
                if not incluir_pagados:
                    q += " AND pagado=0"
                for r in c.execute(q, [tipo] + list(cids)).fetchall():
                    out.append({"id": f"m{r['id']}", "origen": "manual", "empresa": r["empresa"], "empresa_n": "", "fecha": r["fecha"], "vence": r["fecha"],
                                "tercero": r["tercero"] or "", "concepto": r["concepto"], "importe": r["importe"], "nota": r["nota"] or "", "pagado": bool(r["pagado"]),
                                "grupo": r["grupo"]})
            emp = {c["id"]: c["name"].split()[0] for c in odoo.search_read("res.company", [("id", "in", cids)], ["name"], company_ids=cids)}
            for e in out:
                e["empresa_n"] = e["empresa_n"] or emp.get(e["empresa"], "")
            out = [e for e in out if (e["fecha"] or "9999") <= hasta or (e["fecha"] or "") < dt.date.today().isoformat()]
            out.sort(key=lambda e: (e["fecha"] or "9999", -e["importe"]))
            return {"vencimientos": out, "empresas": [{"id": k, "nombre": v} for k, v in emp.items()], "hoy": dt.date.today().isoformat()}
        return guard(run)

    def _sumar_mes(f, n):
        y, m = f.year + (f.month - 1 + n) // 12, (f.month - 1 + n) % 12 + 1
        import calendar
        return dt.date(y, m, min(f.day, calendar.monthrange(y, m)[1]))

    @app.post("/api/vencimientos/manual")
    async def crear(request: Request):
        """{empresa, tipo pagos|cobros, concepto, tercero, fecha, importe, nota, repetir (nº de meses), cada (1 mensual, 3 trimestral…)}"""
        u, cids = ctx(request)
        b = await request.json()
        emp = int(b.get("empresa") or 0)
        if emp not in cids:
            raise HTTPException(403, "Empresa no permitida")
        try:
            f0 = dt.date.fromisoformat(b["fecha"])
            imp = float(str(b["importe"]).replace(",", "."))
        except Exception:
            raise HTTPException(400, "Falta la fecha o el importe")
        if not (b.get("concepto") or "").strip():
            raise HTTPException(400, "Falta el concepto")
        n = max(1, min(int(b.get("repetir") or 1), 120))
        cada = max(1, min(int(b.get("cada") or 1), 12))
        import time
        import secrets
        grupo = secrets.token_hex(4) if n > 1 else None
        with lock, con() as c:
            for i in range(n):
                c.execute("INSERT INTO manuales (empresa, tipo, concepto, tercero, fecha, importe, nota, grupo, usuario, creado) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (emp, "cobros" if b.get("tipo") == "cobros" else "pagos", b["concepto"].strip() + (f" ({i + 1}/{n})" if n > 1 else ""),
                           (b.get("tercero") or "").strip(), _sumar_mes(f0, i * cada).isoformat(), round(imp, 2), b.get("nota") or "", grupo, u, time.time()))
        return {"ok": True, "creados": n}

    @app.post("/api/vencimientos/manual/{vid}")
    async def editar(vid: int, request: Request):
        u, cids = ctx(request)
        b = await request.json()
        with lock, con() as c:
            r = c.execute("SELECT * FROM manuales WHERE id=?", (vid,)).fetchone()
            if not r or r["empresa"] not in cids:
                raise HTTPException(404)
            if b.get("borrar"):
                if b.get("todos") and r["grupo"]:
                    c.execute("DELETE FROM manuales WHERE grupo=? AND pagado=0", (r["grupo"],))
                else:
                    c.execute("DELETE FROM manuales WHERE id=?", (vid,))
                return {"ok": True}
            campos = {k: b[k] for k in ("concepto", "tercero", "fecha", "importe", "nota", "pagado") if k in b}
            if "importe" in campos:
                campos["importe"] = round(float(str(campos["importe"]).replace(",", ".")), 2)
            if "pagado" in campos:
                campos["pagado"] = 1 if campos["pagado"] else 0
            if campos:
                c.execute("UPDATE manuales SET " + ", ".join(f"{k}=?" for k in campos) + " WHERE id=?", list(campos.values()) + [vid])
        return {"ok": True}

    @app.post("/api/vencimientos/odoo/{aml_id}")
    async def marcar_odoo(aml_id: int, request: Request):
        """Nota o fecha prevista de pago para un vencimiento de Odoo (no toca Odoo)."""
        u, _ = ctx(request)
        b = await request.json()
        with lock, con() as c:
            c.execute("INSERT OR REPLACE INTO marcas VALUES (?,?,?,?)", (aml_id, b.get("nota") or "", b.get("fecha_prevista") or None, u))
        return {"ok": True}
