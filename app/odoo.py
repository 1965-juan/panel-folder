"""Cliente mínimo para la API JSON-2 de Odoo 19 (y datos de demostración)."""
import os
import datetime as dt
import httpx


class OdooError(Exception):
    pass


class Odoo:
    def __init__(self):
        self.url = os.environ.get("ODOO_URL", "").rstrip("/")
        self.db = os.environ.get("ODOO_DB", "")
        self.key = os.environ.get("ODOO_KEY", "")
        key_file = os.environ.get("ODOO_KEY_FILE")
        if not self.key and key_file and os.path.exists(key_file):
            import re
            m = re.findall(r"[0-9a-f]{40}", open(key_file, errors="ignore").read())
            self.key = m[0] if m else ""
        self.client = httpx.Client(timeout=40)

    def call(self, model, method, company_ids=None, **params):
        if company_ids:
            ctx = params.setdefault("context", {})
            ctx["allowed_company_ids"] = company_ids
        params.setdefault("context", {}).setdefault("lang", "es_ES")
        r = self.client.post(
            f"{self.url}/json/2/{model}/{method}",
            json=params,
            headers={
                "Authorization": f"bearer {self.key}",
                "X-Odoo-Database": self.db,
                "User-Agent": "PanelFolder/0.1",
            },
        )
        if r.status_code != 200:
            try:
                msg = r.json().get("message") or r.text
            except Exception:
                msg = r.text
            raise OdooError(f"{model}.{method}: {r.status_code} {msg[:300]}")
        return r.json()

    def search_read(self, model, domain, fields, company_ids=None, limit=None, order=None):
        p = {"domain": domain, "fields": fields}
        if limit:
            p["limit"] = limit
        if order:
            p["order"] = order
        return self.call(model, "search_read", company_ids, **p)


class DemoOdoo:
    """Datos inventados para probar el panel sin conexión a Odoo."""

    companies = [
        {"id": 1, "name": "DISTRIPLUS Soluciones y Servicios S.L.U."},
        {"id": 2, "name": "LOGIPAPER CASTELLON S.L.U"},
        {"id": 3, "name": "INGRAF CASTELLON S.L.U"},
        {"id": 4, "name": "FERMASA 2025 S.L.U."},
    ]

    def search_read(self, model, domain, fields, company_ids=None, limit=None, order=None):
        today = dt.date.today()
        d = lambda n: (today + dt.timedelta(days=n)).isoformat()
        clientes = ["Ayuntamiento de Borriol", "Colegio La Salle", "Gestoría Martí", "Facsa", "Clínica Dental Sol"]
        if model == "res.company":
            return self.companies
        if model == "res.partner":
            return [{"id": i + 1, "name": c, "display_name": c} for i, c in enumerate(clientes)]
        if model == "sale.order":
            rows = []
            for i in range(14):
                st = ["draft", "sent", "sale", "sale", "sale"][i % 5]
                rows.append({
                    "id": 100 + i, "name": f"S0{2780 + i}",
                    "partner_id": [i % 5 + 1, clientes[i % 5]],
                    "date_order": d(-i * 3) + " 10:00:00",
                    "validity_date": d(20 - i * 4),
                    "amount_total": round(80 + i * 37.5, 2), "state": st,
                    "delivery_status": ["pending", "partial", "full", "full", "pending"][i % 5] if st == "sale" else False,
                    "invoice_status": ["no", "to invoice", "to invoice", "invoiced", "no"][i % 5] if st == "sale" else "no",
                    "company_id": [1 + i % 3, self.companies[i % 3]["name"]],
                    "user_id": [2, "Dep. Ventas"], "client_order_ref": "",
                })
            return rows
        if model == "account.move":
            return [{
                "id": 500 + i, "name": f"FV/2026/0{900 + i}",
                "partner_id": [i % 5 + 1, clientes[i % 5]],
                "invoice_date": d(-i * 5), "invoice_date_due": d(30 - i * 5),
                "amount_total": round(120 + i * 44.1, 2), "amount_residual": round(120 + i * 44.1, 2),
                "payment_state": "not_paid", "state": "posted",
                "company_id": [1 + i % 3, self.companies[i % 3]["name"]],
                "invoice_origin": f"S0{2782 + i}",
            } for i in range(6)]
        if model == "sale.order.line":
            return [{"id": 1, "product_id": [1, "[115460101] Bolígrafo BIC Cristal azul"], "name": "Bolígrafo BIC Cristal azul",
                     "product_uom_qty": 50, "qty_delivered": 0, "qty_invoiced": 0, "price_unit": 0.32, "price_subtotal": 16.0}]
        if model == "mail.message":
            return [{"id": 1, "date": d(-2) + " 09:12:00", "author_id": [2, "Dep. Ventas"], "body": "<p>Presupuesto creado</p>",
                     "subtype_id": [1, "Nota"], "tracking_value_ids": []}]
        return []
