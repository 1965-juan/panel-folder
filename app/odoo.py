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

    def call_kw(self, model, method, args=None, kwargs=None, company_ids=None):
        """Llamada con argumentos por posición (para métodos propios de módulos, como los del Panel AC de Nextdoo)."""
        kwargs = dict(kwargs or {})
        ctx = kwargs.setdefault("context", {})
        ctx.setdefault("lang", "es_ES")
        if company_ids:
            ctx["allowed_company_ids"] = company_ids
        r = self.client.post(f"{self.url}/jsonrpc", json={
            "jsonrpc": "2.0", "method": "call", "id": 1,
            "params": {"service": "object", "method": "execute_kw",
                       "args": [self.db, self.uid(), self.key, model, method, args or [], kwargs]}})
        j = r.json()
        if j.get("error"):
            d = j["error"].get("data") or {}
            raise OdooError(f"{model}.{method}: {d.get('message') or j['error'].get('message')}"[:400])
        return j.get("result")

    def uid(self):
        if not getattr(self, "_uid", None):
            self._uid = int(os.environ.get("ODOO_UID", "0")) or None
            if not self._uid:
                try:
                    k = self.search_read("res.users.apikeys", [], ["user_id"], limit=1)
                    self._uid = k[0]["user_id"][0]
                except Exception:
                    self._uid = 6  # Dep. Ventas (Juan)
        return self._uid

    def search_read(self, model, domain, fields, company_ids=None, limit=None, order=None):
        p = {"domain": domain, "fields": fields}
        if limit:
            p["limit"] = limit
        if order:
            p["order"] = order
        return self.call(model, "search_read", company_ids, **p)


class DemoOdoo:
    """Datos inventados para probar el panel sin conexión a Odoo."""

    def __init__(self):
        locs = {"8": 6, "52": 0, "46": 3, "34": 2, "40": 0}
        L = lambda lid, pid, name, q, st, badge="Stock", vs="green": {
            "line_id": lid, "move_id": lid, "product_id": pid, "product_name": name, "product_code": name[1:8],
            "product_barcode": "", "qty_ordered": q, "qty_delivered": 0, "qty_pending": q, "stock_by_loc": dict(st),
            "visual_state": vs, "badge_label": badge, "badge_class": "", "prov_requested": badge == "Ped. prov.",
            "qty_on_order": 2 if badge == "Ped. prov." else 0, "pending_print_docs": False}
        self.ac = {
            "locations": [{"id": 8, "name": "Almacén Central", "code": "AC", "is_central": True, "read_only": False},
                          {"id": 52, "name": "IMPRENTA", "code": "IMPT", "is_central": False, "read_only": False},
                          {"id": 46, "name": "LOGIPAPER", "code": "VP", "is_central": False, "read_only": True},
                          {"id": 34, "name": "Tienda 1", "code": "T1", "is_central": False, "read_only": False},
                          {"id": 40, "name": "Tienda 2", "code": "T2", "is_central": False, "read_only": False}],
            "orders": [
                {"order_id": 4805, "order_name": "S02799", "partner_id": 1, "partner_name": "Servicios Ortopédicos Talón S.L.",
                 "has_payment_term": False, "date": "04 nov", "delivery_type": "entrega", "delivery_label": "Entrega a domicilio",
                 "payment_term_name": "", "albaran_authorized": False,
                 "lines": [L(10642, 100658, "[2010104] ETIQUETAS FOLDER ILC 70X30 100 HOJAS", 1, {k: 0 for k in locs}, "Ped. prov.", "purple")]},
                {"order_id": 4806, "order_name": "S02800", "partner_id": 2, "partner_name": "Gestoría Martí",
                 "has_payment_term": True, "date": "04 nov", "delivery_type": "recoge", "delivery_label": "Recoge en tienda",
                 "payment_term_name": "30 días", "albaran_authorized": True,
                 "lines": [L(10650, 11, "[115460101] Bolígrafo BIC Cristal azul", 20, {"8": 50, "52": 0, "46": 0, "34": 4, "40": 0}),
                           L(10651, 12, "[20829] Gorra Karola", 5, {"8": 2, "52": 0, "46": 0, "34": 0, "40": 0}, "Falta stock", "orange")]},
                {"order_id": 4807, "order_name": "S02801", "partner_id": 3, "partner_name": "Colegio La Salle",
                 "has_payment_term": True, "date": "03 nov", "delivery_type": "entrega", "delivery_label": "Entrega a domicilio",
                 "payment_term_name": "Contado", "albaran_authorized": True,
                 "lines": [L(10660, 11, "[115460101] Bolígrafo BIC Cristal azul", 100, {"8": 150, "52": 0, "46": 0, "34": 0, "40": 0})]},
            ],
            "reposicion": [{"picking_id": 29773, "picking_name": "AC/AC/00828", "store_name": "Tienda 1", "date": "04 nov",
                            "lines": [L(90001, 11, "[115460101] Bolígrafo BIC Cristal azul", 12, {"8": 150, "52": 0, "46": 0, "34": 4, "40": 0}),
                                      L(90002, 13, "[3301] Archivador AZ folio", 6, {"8": 0, "52": 0, "46": 0, "34": 0, "40": 3}, "Sin stock", "red")]}],
        }

    def call(self, model, method, company_ids=None, **params):
        if method == "create":
            return self.call_kw(model, "create", [params.get("vals_list", [{}])[0]])
        if model == "sale.order.line" and method in ("unlink", "write"):
            for o in self.ac["orders"]:
                o["lines"] = [l for l in o["lines"] if l["line_id"] not in params.get("ids", [])]
        return True

    def call_kw(self, model, method, args=None, kwargs=None, company_ids=None):
        args = args or []
        if method == "get_panel_data":
            import copy
            return copy.deepcopy(self.ac)
        allines = [(o, l) for o in self.ac["orders"] for l in o["lines"]] + [(p, l) for p in self.ac["reposicion"] for l in p["lines"]]
        if method in ("update_order_line_qty", "update_reposicion_qty"):
            for _, l in allines:
                if l["line_id"] == args[0]:
                    l["qty_ordered"] = l["qty_pending"] = args[1]
            return True
        if method == "cancel_reposicion_move":
            for p in self.ac["reposicion"]:
                p["lines"] = [l for l in p["lines"] if l["move_id"] != args[0]]
            return True
        if method in ("validate_panel_selection", "validate_reposicion_selection"):
            key = "order_line_id" if method == "validate_panel_selection" else "move_id"
            for sel in args[0]:
                for o, l in allines:
                    if l["line_id"] == sel[key]:
                        q = sum(sel["transfers"].values())
                        l["qty_delivered"] += q
                        l["qty_pending"] = max(0, l["qty_ordered"] - l["qty_delivered"])
            for coll in ("orders", "reposicion"):
                for o in self.ac[coll]:
                    o["lines"] = [l for l in o["lines"] if l["qty_pending"] > 0]
                self.ac[coll] = [o for o in self.ac[coll] if o["lines"]]
            return {"picking_names": ["AC/OUT/00999"], "documents": [{"id": 1, "report_name": "stock.report_deliveryslip", "type": "albaran", "name": "AC/OUT/00999"}]}
        if method == "create" and model == "sale.order.line":
            v = args[0]
            for o in self.ac["orders"]:
                if o["order_id"] == v["order_id"]:
                    o["lines"].append({"line_id": 20000 + len(o["lines"]), "product_id": v["product_id"], "product_name": "Artículo añadido",
                                       "product_code": "", "product_barcode": "", "qty_ordered": v["product_uom_qty"], "qty_delivered": 0,
                                       "qty_pending": v["product_uom_qty"], "stock_by_loc": {"8": 10}, "visual_state": "green",
                                       "badge_label": "Stock", "badge_class": "", "prov_requested": False, "qty_on_order": 0, "pending_print_docs": False})
            return 1
        return True

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
                    "user_id": [2, "Dep. Ventas"], "client_order_ref": "", "website_id": [1, "Web"] if i % 4 == 1 else False,
                    "order_line": [1],
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
            return [{"id": 1, "order_id": [101, "S02781"], "product_id": [1, "[115460101] Bolígrafo BIC Cristal azul"], "name": "Bolígrafo BIC Cristal azul",
                     "product_uom_qty": 50, "qty_delivered": 0, "qty_invoiced": 0, "price_unit": 0.32, "price_subtotal": 16.0}]
        if model == "crm.lead":
            return [{"id": 1, "contact_name": "Pilar Forés", "email_from": "pilar@example.com", "phone": "600000000",
                     "description": "lf_service : Regalos de empresa lf_message : Necesito 50 agendas con logo", "create_date": d(-3) + " 10:00:00",
                     "stage_id": [1, "New"], "user_id": False},
                    {"id": 2, "contact_name": "RichardDiers", "email_from": "x@spam.com", "description": "lf_message : earn $50,000 https://telegra.ph",
                     "create_date": d(-1) + " 10:00:00", "stage_id": [1, "New"], "user_id": False}]
        if model == "sale.order" and domain and domain[0][0] == "id":
            return [{"id": 4806, "client_order_ref": "Recoge Marta Gil · 600123123", "note": ""}]
        if model == "product.product":
            return [{"id": 11, "default_code": "115460101", "name": "Bolígrafo BIC Cristal azul", "lst_price": 0.45, "product_tmpl_id": [21, "x"]},
                    {"id": 12, "default_code": "20829", "name": "Gorra Karola", "lst_price": 2.9, "product_tmpl_id": [22, "x"]}]
        if model == "product.pricelist.item":
            return [{"product_tmpl_id": [21, "x"], "product_id": False, "min_quantity": 50, "fixed_price": 0.32},
                    {"product_tmpl_id": [21, "x"], "product_id": False, "min_quantity": 500, "fixed_price": 0.27}]
        if model == "mail.message":
            return [{"id": 1, "date": d(-2) + " 09:12:00", "author_id": [2, "Dep. Ventas"], "body": "<p>Presupuesto creado</p>",
                     "subtype_id": [1, "Nota"], "tracking_value_ids": []}]
        return []
