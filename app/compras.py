"""Compras: subir albaranes y facturas de proveedor (PDF), recibir albaranes contra el pedido de compra,
revisión de facturas en borrador para Mayte y lista de facturas recurrentes del mes.

- Las facturas se suben a Odoo como borrador y Odoo las lee solo (OCR activado en todas las empresas).
- Los albaranes se guardan en el panel y se enlazan con la recepción del pedido de compra; al recibirlos,
  el PDF queda adjunto a la recepción en Odoo.
"""
import base64
import datetime as dt
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import Request, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse


def setup(app, *, data_dir: Path, odoo, current_user, scope, guard, es_admin, demo):
    carpeta = data_dir / "compras"
    carpeta.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "compras.db"
    lock = threading.Lock()

    def con():
        c = sqlite3.connect(db_path, timeout=20)
        c.row_factory = sqlite3.Row
        c.executescript("""
        CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, tipo TEXT, nombre TEXT, fichero TEXT, empresa INTEGER,
            subido REAL, usuario TEXT, estado TEXT, odoo_model TEXT, odoo_id INTEGER, nota TEXT);
        CREATE TABLE IF NOT EXISTS recurrentes (id INTEGER PRIMARY KEY AUTOINCREMENT, empresa INTEGER, empresa_nombre TEXT,
            partner_id INTEGER, partner_nombre TEXT, concepto TEXT, importe REAL, dia INTEGER, activo INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS recurrentes_marcas (rid INTEGER, mes TEXT, nota TEXT, PRIMARY KEY (rid, mes));
        CREATE TABLE IF NOT EXISTS factura_marcas (move_id INTEGER PRIMARY KEY, tipo TEXT, usuario TEXT, cuando REAL, nota TEXT);
        """)
        try:
            c.execute("ALTER TABLE docs ADD COLUMN lectura TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE docs ADD COLUMN compra TEXT")
        except sqlite3.OperationalError:
            pass
        return c

    def ctx(request, company="all"):
        u = current_user(request)
        cids, _ = scope(u, company)
        return u, cids

    def una_empresa(request, company):
        u, cids = ctx(request, company)
        if company in ("", "all", None):
            return u, cids[0], cids
        return u, int(company), cids

    # ---------- subir PDF ----------
    def recuperar_huerfanos():
        """PDF guardados en disco sin fila en la base (subidas que fallaron al guardar): se vuelven a dar de alta,
        enlazados con su factura de Odoo si se llegó a crear (se busca el adjunto por su huella)."""
        import hashlib
        try:
            with con() as c:
                conocidos = {r["fichero"] for r in c.execute("SELECT fichero FROM docs").fetchall()}
            for f in sorted(carpeta.glob("*.pdf")):
                if f.name in conocidos:
                    continue
                datos = f.read_bytes()
                sha = hashlib.sha1(datos).hexdigest()
                tipo, nombre, emp, model, oid, estado = "albaran", f"recuperado_{time.strftime('%d%m_%H%M', time.localtime(f.stat().st_mtime))}.pdf", 1, None, None, "pendiente"
                try:
                    at = odoo.search_read("ir.attachment", [("checksum", "=", sha), ("res_model", "=", "account.move"), ("res_id", "!=", 0)],
                                          ["name", "res_id", "company_id"], company_ids=[1, 2, 3, 4, 5], limit=1)
                    if at:
                        mv = odoo.search_read("account.move", [("id", "=", at[0]["res_id"])], ["company_id"], company_ids=[1, 2, 3, 4, 5])
                        tipo, nombre, model, oid, estado = "factura", at[0]["name"], "account.move", at[0]["res_id"], "en_odoo"
                        emp = mv[0]["company_id"][0] if mv else 1
                    else:
                        at = odoo.search_read("ir.attachment", [("checksum", "=", sha)], ["name"], company_ids=[1, 2, 3, 4, 5], limit=1)
                        if at:
                            nombre = at[0]["name"].replace("MAIL_", "")
                except Exception:
                    pass
                with lock, con() as c:
                    c.execute("INSERT OR IGNORE INTO docs (id, tipo, nombre, fichero, empresa, subido, usuario, estado, odoo_model, odoo_id, nota) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (f.stem, tipo, nombre, f.name, emp, f.stat().st_mtime, "recuperado", estado, model, oid, None))
        except Exception as e:
            print("recuperar_huerfanos:", e)
    if not demo:
        threading.Thread(target=recuperar_huerfanos, daemon=True).start()

    LEYENDO = set()

    def leer_en_segundo_plano(did):
        if demo or did in LEYENDO:
            return
        LEYENDO.add(did)

        def run():
            try:
                with con() as c:
                    r = c.execute("SELECT fichero, lectura FROM docs WHERE id=?", (did,)).fetchone()
                if not r or r["lectura"]:
                    return
                datos = leer_con_claude((carpeta / r["fichero"]).read_bytes())
                with lock, con() as c:
                    if datos.get("error"):
                        c.execute("UPDATE docs SET lectura=? WHERE id=?", (json.dumps({"error": str(datos["error"])[:300]}, ensure_ascii=False), did))
                    else:
                        c.execute("UPDATE docs SET lectura=? WHERE id=?", (json.dumps(datos, ensure_ascii=False), did))
                if not datos.get("error"):
                    try:
                        crear_factura_odoo(did, datos)
                    except Exception as e:
                        with lock, con() as c:
                            c.execute("UPDATE docs SET estado='error', nota=? WHERE id=?", (f"No se pudo crear la factura en Odoo: {e}"[:300], did))
            except Exception as e:
                try:
                    with lock, con() as c:
                        c.execute("UPDATE docs SET lectura=? WHERE id=?", (json.dumps({"error": str(e)[:300]}, ensure_ascii=False), did))
                except Exception:
                    pass
            finally:
                LEYENDO.discard(did)
        threading.Thread(target=run, daemon=True).start()

    TODAS = [1, 2, 3, 4, 5]
    REPARADAS = set()

    def poner_pdf(model, rid, nombre, datos, cids):
        """Adjunta el PDF al registro de Odoo de forma que se VEA: mensaje en el historial con el PDF y PDF principal
        (el que Odoo enseña al lado de la factura)."""
        att = odoo.call("ir.attachment", "create", cids, vals_list=[{"name": nombre or "documento.pdf", "res_model": model, "res_id": rid,
                                                                     "mimetype": "application/pdf", "datas": base64.b64encode(datos).decode()}])
        att = att[0] if isinstance(att, list) else att
        try:
            odoo.call(model, "message_post", cids, ids=[rid], body=f"Documento subido desde el Panel Folder: {nombre}", attachment_ids=[att])
        except Exception:
            pass
        try:
            odoo.call(model, "write", cids, ids=[rid], vals={"message_main_attachment_id": att})
        except Exception:
            pass
        return att

    def arreglar_pdf_principal(model, rid, cids):
        """Facturas a las que se adjuntó el PDF pero Odoo no lo enseña: se marca como PDF principal."""
        m = odoo.search_read(model, [("id", "=", rid)], ["message_main_attachment_id"], company_ids=cids)
        if not m or m[0].get("message_main_attachment_id"):
            return False
        at = odoo.search_read("ir.attachment", [("res_model", "=", model), ("res_id", "=", rid), ("mimetype", "=", "application/pdf")], ["id"], company_ids=cids, limit=1, order="id")
        if not at:
            return False
        odoo.call(model, "write", cids, ids=[rid], vals={"message_main_attachment_id": at[0]["id"]})
        return True

    def pedido_de_marca(marca):
        """Para marcas 'pedido' (factura de una empresa con pedido de otra): (nombre, recibido_entero)."""
        try:
            n = json.loads(marca.get("nota") or "{}")
        except Exception:
            return None, False
        po = odoo.search_read("purchase.order", [("id", "=", n.get("pedido_id"))], ["name", "receipt_status"], company_ids=TODAS)
        return (po[0]["name"], po[0]["receipt_status"] == "full") if po else (n.get("pedido"), False)

    def empresa_del_pdf(lec, por_defecto):
        nif = re.sub(r"[^A-Z0-9]", "", (lec.get("nif_cliente") or "").upper())
        nif = nif[2:] if nif.startswith("ES") else nif
        cs = odoo.search_read("res.company", [], ["name", "vat"], company_ids=TODAS)
        if len(nif) >= 8:
            for c in cs:
                v = re.sub(r"[^A-Z0-9]", "", (c.get("vat") or "").upper())
                if v.endswith(nif):
                    return c["id"]
        nom = (lec.get("cliente") or "").lower()
        for c in cs:
            if c["name"].split()[0].lower() in nom:
                return c["id"]
        return por_defecto

    def crear_factura_odoo(did, lec):
        """Crea la factura de proveedor en borrador en la empresa a la que va dirigido el PDF, con sus líneas, y adjunta el PDF."""
        with con() as c:
            r = c.execute("SELECT * FROM docs WHERE id=?", (did,)).fetchone()
        if not r or r["tipo"] != "factura" or r["odoo_id"] or (lec.get("tipo") and lec["tipo"] not in ("factura", "")):
            return
        cid = empresa_del_pdf(lec, r["empresa"])
        prov = _buscar_proveedor(lec, TODAS)
        # ¿ya existe esa factura del proveedor en Odoo? (mismo proveedor y nº) -> no se duplica, se enlaza
        if prov and (lec.get("numero") or "").strip():
            ya = odoo.search_read("account.move", [("partner_id", "child_of", prov["id"]), ("ref", "=ilike", lec["numero"].strip()),
                                                   ("move_type", "in", ["in_invoice", "in_refund"]), ("state", "!=", "cancel")], ["name", "company_id"], company_ids=TODAS, limit=1)
            if ya:
                with lock, con() as c:
                    c.execute("UPDATE docs SET empresa=?, estado='en_odoo', odoo_model='account.move', odoo_id=?, nota=? WHERE id=?",
                              (ya[0]["company_id"][0], ya[0]["id"], f"Esta factura ya estaba en Odoo ({ya[0]['name'] or 'borrador'}): no se ha duplicado", did))
                return
        abono = _num(lec.get("total")) < 0
        tax_cache = {}

        def imp(iva):
            if iva not in tax_cache:
                ts = odoo.search_read("account.tax", [("type_tax_use", "=", "purchase"), ("amount", "=", iva), ("company_id", "=", cid), ("amount_type", "=", "percent")],
                                      ["name"], company_ids=[cid], order="sequence, id")
                pref = [t for t in ts if re.search(r"\bG\b|bienes", t["name"], re.I)] or [t for t in ts if not re.search(r"servic|invers|intra|import|ISP|exent|recargo", t["name"], re.I)] or ts
                tax_cache[iva] = [pref[0]["id"]] if pref else []
            return tax_cache[iva]
        lineas = []
        for l in lec.get("lineas") or []:
            q, pr = _num(l.get("cantidad")) or 1, _num(l.get("precio"))
            if not pr and _num(l.get("importe")):
                pr = _num(l["importe"]) / q
            nombre = (f"[{l['ref']}] " if l.get("ref") else "") + (l.get("descripcion") or "Según factura")
            lineas.append([0, 0, {"name": nombre[:250], "quantity": abs(q), "price_unit": abs(pr), "discount": _num(l.get("descuento")),
                                  "tax_ids": [[6, 0, imp(_num(l.get("iva")) or 21.0)]]}])
        if _num(lec.get("portes")):
            lineas.append([0, 0, {"name": "Portes", "quantity": 1, "price_unit": abs(_num(lec["portes"])), "tax_ids": [[6, 0, imp(21.0)]]}])
        if not lineas and _num(lec.get("base")):
            lineas.append([0, 0, {"name": "Según factura adjunta", "quantity": 1, "price_unit": abs(_num(lec["base"])), "tax_ids": [[6, 0, imp(21.0)]]}])
        jr = odoo.search_read("account.journal", [("type", "=", "purchase"), ("company_id", "=", cid)], ["id"], company_ids=[cid], limit=1)
        vals = {"move_type": "in_refund" if abono else "in_invoice", "journal_id": jr[0]["id"], "invoice_line_ids": lineas}
        if prov:
            vals["partner_id"] = prov["id"]
        if lec.get("numero"):
            vals["ref"] = lec["numero"]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", lec.get("fecha") or ""):
            vals["invoice_date"] = lec["fecha"]
        mid = odoo.call("account.move", "create", [cid], vals_list=[vals], context={"default_move_type": vals["move_type"]})
        mid = mid[0] if isinstance(mid, list) else mid
        poner_pdf("account.move", mid, r["nombre"] or "factura.pdf", (carpeta / r["fichero"]).read_bytes(), [cid])
        with lock, con() as c:
            c.execute("UPDATE docs SET empresa=?, estado='en_odoo', odoo_model='account.move', odoo_id=? WHERE id=?", (cid, mid, did))

    def ya_subido(datos):
        """Mismo PDF ya en la bandeja (no descartado): se compara el contenido, no el nombre."""
        import hashlib
        h = hashlib.sha1(datos).hexdigest()
        with con() as c:
            rs = c.execute("SELECT id, nombre, fichero, subido FROM docs WHERE estado != 'descartado' AND subido > ?", (time.time() - 120 * 86400,)).fetchall()
        for r in rs:
            f = carpeta / r["fichero"]
            try:
                if f.exists() and f.stat().st_size == len(datos) and hashlib.sha1(f.read_bytes()).hexdigest() == h:
                    return dict(r)
            except Exception:
                pass
        return None

    def alta_doc(nombre, datos, tipo, empresa, usuario):
        """Da de alta un PDF en la bandeja de Compras (lo usa también el correo) y lo manda a leer."""
        did = secrets.token_hex(8)
        (carpeta / f"{did}.pdf").write_bytes(datos)
        with lock, con() as c:
            c.execute("INSERT INTO docs (id, tipo, nombre, fichero, empresa, subido, usuario, estado, odoo_model, odoo_id, nota) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (did, tipo, nombre, f"{did}.pdf", empresa, time.time(), usuario, "pendiente", None, None, None))
        leer_en_segundo_plano(did)
        return did
    app.state.compras_alta = alta_doc

    @app.post("/api/compras/subir")
    async def subir(request: Request, tipo: str = Form(...), company: str = Form("all"), archivos: list[UploadFile] = File(...)):
        u, cid, cids = una_empresa(request, company)
        if tipo not in ("albaran", "factura"):
            raise HTTPException(400, "Tipo no válido")
        out = []
        for f in archivos:
            datos = await f.read()
            if not datos:
                continue
            if not (f.filename or "").lower().endswith(".pdf") and not datos.startswith(b"%PDF"):
                out.append({"nombre": f.filename, "error": "No es un PDF"})
                continue
            igual = ya_subido(datos)
            if igual:
                out.append({"nombre": f.filename, "error": f"Este PDF ya está subido ({igual['nombre']}, {time.strftime('%d/%m %H:%M', time.localtime(igual['subido']))}). No lo subo otra vez."})
                continue
            did = secrets.token_hex(8)
            fich = carpeta / f"{did}.pdf"
            fich.write_bytes(datos)
            item = {"id": did, "nombre": f.filename, "tipo": tipo}
            estado, model, oid = "pendiente", None, None
            if tipo == "factura" and not demo and False:  # ahora se crea tras leer el PDF (empresa según el CIF del destinatario)
                try:
                    jr = odoo.search_read("account.journal", [("type", "=", "purchase"), ("company_id", "=", cid)], ["id"], company_ids=[cid], limit=1)
                    att = odoo.call("ir.attachment", "create", [cid], vals_list=[{
                        "name": f.filename or "factura.pdf", "datas": base64.b64encode(datos).decode(), "mimetype": "application/pdf"}])
                    att = att[0] if isinstance(att, list) else att
                    act = odoo.call("account.journal", "create_document_from_attachment", [cid], ids=[jr[0]["id"]], attachment_ids=[att],
                                    context={"default_move_type": "in_invoice", "allowed_company_ids": [cid]})
                    oid = (act or {}).get("res_id")
                    if not oid:
                        dom = (act or {}).get("domain") or []
                        ids = next((d[2] for d in dom if isinstance(d, (list, tuple)) and d[0] == "id"), [])
                        oid = ids[0] if ids else None
                    model, estado = "account.move", "en_odoo"
                except Exception as e:
                    estado = "error"
                    item["error"] = f"No se pudo subir a Odoo: {e}"[:300]
            elif tipo == "factura" and demo:
                model, oid, estado = "account.move", 9001, "en_odoo"
            with lock, con() as c:
                c.execute("INSERT INTO docs (id, tipo, nombre, fichero, empresa, subido, usuario, estado, odoo_model, odoo_id, nota) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (did, tipo, f.filename, fich.name, cid, time.time(), u, estado, model, oid, item.get("error")))
            item.update(estado=estado, odoo_id=oid)
            out.append(item)
            leer_en_segundo_plano(did)
        return out

    @app.get("/api/compras/docs")
    def docs(request: Request, tipo: str = "", estado: str = "", company: str = "all"):
        _, cids = ctx(request, company)
        q = "SELECT * FROM docs WHERE empresa IN (%s)" % ",".join("?" * len(cids))
        args = list(cids)
        if tipo:
            q += " AND tipo=?"; args.append(tipo)
        if estado:
            q += " AND estado=?"; args.append(estado)
        with con() as c:
            out = []
            for r in c.execute(q + " ORDER BY subido DESC LIMIT 200", args).fetchall():
                d = dict(r)
                d.pop("lectura", None)
                d["compra"] = json.loads(d["compra"]) if d.get("compra") else None
                out.append(d)
            return out

    @app.get("/api/compras/pdf/{did}")
    def pdf(did: str, request: Request):
        current_user(request)
        with con() as c:
            r = c.execute("SELECT fichero, nombre FROM docs WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404)
        return FileResponse(carpeta / r["fichero"], media_type="application/pdf", filename=r["nombre"],
                            content_disposition_type="inline")

    def leer_con_claude(datos):
        import os as _os
        import httpx as _hx
        cf = data_dir / "claude_clave.txt"
        k = re.findall(r"sk-ant-[A-Za-z0-9_\-]+", cf.read_text(errors="ignore")) if cf.exists() else []
        if not k:
            raise HTTPException(400, "Falta la clave de Claude en el NAS")
        pide = ("Lee este documento de proveedor (factura, albarán o confirmación de pedido) y devuelve SOLO un JSON con: "
                '{"tipo": "factura|albaran|pedido", "proveedor": "", "nif_proveedor": "", "cliente": "", "nif_cliente": "", "numero": "", "fecha": "AAAA-MM-DD", '
                '"lineas": [{"ref": "", "ean": "", "descripcion": "", "cantidad": 0, "precio": 0, "descuento": 0, "iva": 21, "importe": 0}], '
                '"base": 0, "iva_total": 0, "total": 0, "portes": 0}. Precios unitarios sin IVA y ya con el descuento de línea aplicado en "precio" '
                'solo si el documento no muestra el descuento aparte. "cliente"/"nif_cliente" es a quién va dirigido el documento (el comprador). '
                'En un abono o factura rectificativa los importes van en negativo. Números con punto decimal. No inventes nada: si un dato no está, déjalo vacío.')
        r = _hx.post(_os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages", timeout=180, headers={
            "x-api-key": k[0], "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": _os.environ.get("PANEL_MODEL", "claude-sonnet-5-5"), "max_tokens": 8000, "messages": [{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(datos).decode()}},
                {"type": "text", "text": pide}]}]})
        if r.status_code != 200:
            raise HTTPException(502, f"Claude respondió {r.status_code}: {r.text[:200]}")
        txt = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        m = re.search(r"\{[\s\S]*\}", txt)
        return json.loads(m.group(0)) if m else {"error": txt[:500]}

    @app.post("/api/compras/docs/{did}/leer")
    def doc_leer(did: str, request: Request):
        """Lee el PDF con Claude y devuelve sus datos (proveedor, número, líneas…)."""
        current_user(request)
        with con() as c:
            r = c.execute("SELECT fichero FROM docs WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404)
        datos = leer_con_claude((carpeta / r["fichero"]).read_bytes())
        with lock, con() as c:
            c.execute("UPDATE docs SET lectura=? WHERE id=?", (json.dumps(datos, ensure_ascii=False), did))
        return datos

    @app.post("/api/compras/docs/{did}/a_factura")
    def doc_a_factura(did: str, request: Request, company: str = "all"):
        """Un PDF subido como albarán que en realidad es una factura: lo manda a Odoo como factura en borrador."""
        u, cid, cids = una_empresa(request, company)
        with con() as c:
            r = c.execute("SELECT * FROM docs WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404)
        datos = (carpeta / r["fichero"]).read_bytes()

        def run():
            emp = r["empresa"] or cid
            jr = odoo.search_read("account.journal", [("type", "=", "purchase"), ("company_id", "=", emp)], ["id"], company_ids=[emp], limit=1)
            att = odoo.call("ir.attachment", "create", [emp], vals_list=[{"name": r["nombre"], "datas": base64.b64encode(datos).decode(), "mimetype": "application/pdf"}])
            att = att[0] if isinstance(att, list) else att
            act = odoo.call("account.journal", "create_document_from_attachment", [emp], ids=[jr[0]["id"]], attachment_ids=[att],
                                context={"default_move_type": "in_invoice", "allowed_company_ids": [emp]})
            oid = (act or {}).get("res_id")
            with lock, con() as c:
                c.execute("UPDATE docs SET tipo='factura', estado='en_odoo', odoo_model='account.move', odoo_id=? WHERE id=?", (oid, did))
            return {"ok": True, "factura_id": oid}
        return guard(run)

    # ---------- PDF -> pedido de compra (y factura en borrador enlazada) ----------
    def _doc(did):
        with con() as c:
            r = c.execute("SELECT * FROM docs WHERE id=?", (did,)).fetchone()
        if not r:
            raise HTTPException(404, "Documento no encontrado")
        return r

    def _lectura(r, releer=False):
        if r["lectura"] and not releer and not json.loads(r["lectura"]).get("error"):
            return json.loads(r["lectura"])
        datos = leer_con_claude((carpeta / r["fichero"]).read_bytes())
        if datos.get("error"):
            raise HTTPException(502, "No he podido leer el PDF: " + str(datos["error"])[:200])
        with lock, con() as c:
            c.execute("UPDATE docs SET lectura=? WHERE id=?", (json.dumps(datos, ensure_ascii=False), r["id"]))
        return datos

    def _num(v):
        try:
            return float(str(v).replace(",", ".")) if v not in (None, "") else 0.0
        except ValueError:
            return 0.0

    def _buscar_proveedor(lec, cids):
        nif = re.sub(r"[^A-Z0-9]", "", (lec.get("nif_proveedor") or "").upper())
        if nif.startswith("ES"):
            nif = nif[2:]
        if len(nif) >= 8:
            ps = odoo.search_read("res.partner", [("vat", "ilike", nif), ("parent_id", "=", False)], ["display_name", "vat"], company_ids=cids, limit=5)
            if ps:
                return ps[0]
        nom = (lec.get("proveedor") or "").strip()
        palabras = [w for w in re.findall(r"[\wÁÉÍÓÚÑáéíóúñ]{4,}", nom) if w.upper() not in ("S.L.", "SLU", "S.A.", "SOCIEDAD", "LIMITADA")]
        if palabras:
            ps = odoo.search_read("res.partner", [("name", "ilike", palabras[0]), ("parent_id", "=", False)], ["display_name", "vat"], company_ids=cids, limit=5)
            if len(ps) == 1:
                return ps[0]
        return None

    # ---------- Cuadrar un albarán/factura con el pedido de compra abierto del proveedor ----------
    # Procedimiento de Juan: identificar cada línea (EAN > ref. interna/código proveedor > nombre), dejar el pedido
    # exactamente como el documento (cantidad, precio, dto.), añadir lo que falte, pasar lo que no viene a un pedido
    # "Pendiente de Pxxxxx", poner el nº del documento en la referencia del proveedor y confirmar. La entrada queda
    # en Recepciones para que almacén la compruebe. No se crean artículos nuevos desde aquí.
    _campos = {}

    def _tiene_campo(model, campo, cid):
        if model not in _campos:
            _campos[model] = set(odoo.call(model, "fields_get", [cid], attributes=["type"]).keys())
        return campo in _campos[model]

    def _impuesto(cid, iva, cache={}):
        k = (cid, round(iva, 2))
        if k not in cache:
            ts = odoo.search_read("account.tax", [("type_tax_use", "=", "purchase"), ("amount", "=", iva), ("company_id", "=", cid), ("amount_type", "=", "percent")],
                                  ["name"], company_ids=[cid], order="sequence, id")
            pref = [t for t in ts if re.search(r"\bG\b|bienes", t["name"], re.I)] or [t for t in ts if not re.search(r"servic|invers|intra|import|ISP|exent|recargo", t["name"], re.I)] or ts
            cache[k] = pref[0]["id"] if pref else None
        return cache[k]

    def _ean13(base12):
        s = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base12))
        return base12 + str((10 - s % 10) % 10)

    # reglas de EAN por proveedor (partner_id): Dohe 8421938 + código 5 cifras; Poessa 843017 + código 6 cifras
    EAN_PROV = {7290: ("8421938", 5), 7140: ("843017", 6)}

    def _tokens(t):
        return set(w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) >= 2)

    def _identificar(l, partner_id, cids, po_lines):
        """Devuelve (product_id, nombre, como) o (None, None, None)."""
        ref = str(l.get("ref") or "").strip()
        ean = re.sub(r"\D", "", str(l.get("ean") or ""))
        if not ean and partner_id in EAN_PROV and ref.isdigit():
            pre, n = EAN_PROV[partner_id]
            if len(ref) <= n:
                ean = _ean13(pre + ref.zfill(n))
        if len(ean) >= 8:
            pp = odoo.search_read("product.product", [("barcode", "=", ean)], ["display_name"], company_ids=cids, limit=1)
            if not pp:
                try:
                    pb = odoo.search_read("product.barcode", [("name", "=", ean)], ["product_id"], company_ids=cids, limit=1)
                    if pb and pb[0].get("product_id"):
                        pp = [{"id": pb[0]["product_id"][0], "display_name": pb[0]["product_id"][1]}]
                except Exception:
                    pass
            if pp:
                return pp[0]["id"], pp[0]["display_name"], "EAN"
        if ref:
            pp = odoo.search_read("product.product", [("default_code", "=ilike", ref)], ["display_name"], company_ids=cids, limit=2)
            if len(pp) == 1:
                return pp[0]["id"], pp[0]["display_name"], "referencia"
            if partner_id:
                si = odoo.search_read("product.supplierinfo", [("partner_id", "=", partner_id), ("product_code", "=", ref)], ["product_id", "product_tmpl_id"], company_ids=cids, limit=1)
                if si:
                    if si[0].get("product_id"):
                        return si[0]["product_id"][0], si[0]["product_id"][1], "cód. proveedor"
                    pp = odoo.search_read("product.product", [("product_tmpl_id", "=", si[0]["product_tmpl_id"][0])], ["display_name"], company_ids=cids, limit=1)
                    if pp:
                        return pp[0]["id"], pp[0]["display_name"], "cód. proveedor"
        # contra las líneas del pedido: que estén todos los números de la descripción y alguna palabra
        desc = l.get("descripcion") or ""
        td = _tokens(desc + " " + ref)
        nums = {w for w in td if re.search(r"\d", w)}
        mejor, punt = None, 0
        for pl in po_lines:
            tp = _tokens(pl["name"] + " " + (pl["product_id"][1] if pl.get("product_id") else ""))
            if nums and not (nums & tp):
                continue
            comunes = len(td & tp)
            if comunes >= 2 and comunes > punt:
                mejor, punt = pl, comunes
        if mejor:
            return mejor["product_id"][0], mejor["product_id"][1], "nombre (revisar)"
        if len(desc) >= 6:
            pp = odoo.search_read("product.product", [("name", "=ilike", desc)], ["display_name"], company_ids=cids, limit=2)
            if len(pp) == 1:
                return pp[0]["id"], pp[0]["display_name"], "nombre"
        return None, None, None

    def _pedidos_abiertos(partner_id, cids):
        return odoo.search_read("purchase.order", [("partner_id", "child_of", partner_id), ("state", "in", ["draft", "sent", "to approve"])],
                                ["name", "date_order", "amount_untaxed", "origin", "company_id", "partner_ref"], company_ids=cids, order="id asc")

    def _lineas_pedido(po_id, cids):
        return odoo.search_read("purchase.order.line", [("order_id", "=", po_id), ("display_type", "=", False)],
                                ["product_id", "name", "product_qty", "price_unit", "discount", "price_subtotal"], company_ids=cids)

    @app.post("/api/compras/docs/{did}/cuadrar")
    async def doc_cuadrar(did: str, request: Request, releer: int = 0, pedido: int = 0):
        """Lee el documento y lo cruza con el pedido abierto del proveedor. Solo consulta, no cambia nada."""
        u, cids = ctx(request, "all")
        r = _doc(did)
        if r["empresa"] not in cids:
            raise HTTPException(403, "Documento de otra empresa")
        b = {}
        try:
            b = await request.json()
        except Exception:
            pass

        def run():
            lec = _lectura(r, bool(releer))
            num = (lec.get("numero") or "").strip()
            info = {"doc": {"id": r["id"], "nombre": r["nombre"]},
                    "lectura": {k: lec.get(k) for k in ("tipo", "proveedor", "nif_proveedor", "numero", "fecha", "base", "iva_total", "total", "portes")}}
            if r["compra"]:
                info["compra"] = json.loads(r["compra"]); return info
            if num:
                ya = odoo.search_read("purchase.order", [("partner_ref", "=ilike", num), ("state", "in", ["purchase", "done"])], ["name"], company_ids=cids, limit=1)
                if ya:
                    info["ya_cuadrado"] = ya[0]["name"]
                    return info
            prov = None
            if b.get("partner_id"):
                pp = odoo.search_read("res.partner", [("id", "=", int(b["partner_id"]))], ["display_name", "vat"], company_ids=cids)
                prov = pp[0] if pp else None
            prov = prov or _buscar_proveedor(lec, cids)
            info["proveedor"] = prov
            if not prov:
                return info
            abiertos = _pedidos_abiertos(prov["id"], cids)
            info["pedidos_abiertos"] = [{"id": p["id"], "name": p["name"], "fecha": (p["date_order"] or "")[:10], "base": p["amount_untaxed"],
                                         "origen": p.get("origin") or "", "empresa": p["company_id"][1]} for p in abiertos]
            info["confirmados"] = [p["name"] for p in odoo.search_read("purchase.order", [("partner_id", "child_of", prov["id"]), ("state", "=", "purchase"),
                                                                                           ("receipt_status", "in", ["pending", "partial"])], ["name"], company_ids=cids, limit=5)]
            # pedido elegido: el indicado o el que más líneas casa
            lineas_doc = lec.get("lineas") or []
            cand = [p for p in abiertos if not pedido or p["id"] == pedido]
            mejor = None
            for p in cand:
                pls = _lineas_pedido(p["id"], cids)
                ids = {pl["product_id"][0] for pl in pls if pl.get("product_id")}
                res = []
                for l in lineas_doc:
                    pid, nom, como = _identificar(l, prov["id"], cids, pls)
                    res.append((l, pid, nom, como))
                score = sum(1 for x in res if x[1] in ids)
                if mejor is None or score > mejor[0]:
                    mejor = (score, p, pls, res)
            if mejor is None:  # sin pedido abierto: identificar igualmente para crear uno con lo identificado
                res = [(l,) + _identificar(l, prov["id"], cids, []) for l in lineas_doc]
                mejor = (0, None, [], res)
            _, po, pls, res = mejor
            usados, out = set(), []
            for l, pid, nom, como in res:
                pl = next((x for x in pls if x.get("product_id") and x["product_id"][0] == pid and x["id"] not in usados), None)
                if pl:
                    usados.add(pl["id"])
                out.append({"ref": str(l.get("ref") or ""), "ean": str(l.get("ean") or ""), "descripcion": l.get("descripcion") or "",
                            "cantidad": _num(l.get("cantidad")), "precio": _num(l.get("precio")), "descuento": _num(l.get("descuento")),
                            "iva": _num(l.get("iva")) or 21.0, "product_id": pid, "producto": nom, "como": como,
                            "po_line": pl["id"] if pl else None, "pedido_cant": pl["product_qty"] if pl else None,
                            "pedido_precio": pl["price_unit"] if pl else None, "pedido_dto": pl.get("discount") if pl else None})
            info["pedido"] = {"id": po["id"], "name": po["name"], "origen": po.get("origin") or "", "empresa": po["company_id"][0]} if po else None
            info["lineas"] = out
            info["no_vienen"] = [{"id": x["id"], "producto": x["product_id"][1] if x.get("product_id") else x["name"], "cantidad": x["product_qty"],
                                  "precio": x["price_unit"]} for x in pls if x["id"] not in usados]
            return info
        return guard(run)

    @app.post("/api/compras/docs/{did}/cuadrar/aplicar")
    async def doc_cuadrar_aplicar(did: str, request: Request):
        u, cids = ctx(request, "all")
        b = await request.json()
        r = _doc(did)
        if r["empresa"] not in cids:
            raise HTTPException(403, "Documento de otra empresa")
        if r["compra"]:
            raise HTTPException(400, "Este documento ya está cuadrado: " + json.loads(r["compra"]).get("pedido", ""))
        partner_id = int(b.get("partner_id") or 0)
        po_id = int(b.get("pedido_id") or 0)
        lineas = [l for l in (b.get("lineas") or []) if (l.get("product_id") or l.get("crear")) and _num(l.get("cantidad")) > 0 and not l.get("saltar")]
        if not partner_id or not lineas:
            raise HTTPException(400, "Falta el proveedor o no hay líneas identificadas")

        def run():
            lec = json.loads(r["lectura"]) if r["lectura"] else {}
            avisos = []
            # artículos nuevos: solo si traen EAN y ese EAN no existe en Odoo (así no se duplica nada)
            creados = []
            for l in lineas:
                if l.get("product_id") or not l.get("crear"):
                    continue
                ean = re.sub(r"\D", "", str(l.get("ean") or ""))
                if len(ean) < 8:
                    raise HTTPException(400, f"«{l.get('descripcion')}» no trae código de barras: búscalo en Odoo o márcalo como no meter")
                ya = odoo.search_read("product.product", [("barcode", "=", ean)], ["display_name"], company_ids=TODAS, limit=1)
                if ya:
                    l["product_id"], l["producto"] = ya[0]["id"], ya[0]["display_name"]
                    continue
                vals = {"name": (l.get("descripcion") or l.get("ref") or "Artículo nuevo")[:120], "barcode": ean, "purchase_ok": True, "sale_ok": True,
                        "type": "consu", "standard_price": round(_num(l.get("precio")) * (1 - _num(l.get("descuento")) / 100), 4),
                        "seller_ids": [[0, 0, {"partner_id": partner_id, "product_code": l.get("ref") or False, "price": _num(l.get("precio")),
                                               "discount": _num(l.get("descuento")), "company_id": 1}]]}
                if _tiene_campo("product.product", "is_storable", 1):
                    vals["is_storable"] = True
                if l.get("ref") and not odoo.search_read("product.product", [("default_code", "=", l["ref"])], ["id"], company_ids=TODAS, limit=1):
                    vals["default_code"] = l["ref"]
                pid = odoo.call("product.product", "create", [1], vals_list=[vals])
                l["product_id"] = pid[0] if isinstance(pid, list) else pid
                l["producto"] = vals["name"]
                creados.append(vals["name"])
            if po_id:
                po = odoo.search_read("purchase.order", [("id", "=", po_id)], ["name", "state", "company_id", "picking_type_id", "origin"], company_ids=cids)
                if not po or po[0]["state"] not in ("draft", "sent", "to approve"):
                    raise HTTPException(400, "El pedido ya no está abierto: vuelve a abrir el documento")
                po = po[0]
                cid = po["company_id"][0]
            else:
                cid = 1 if 1 in cids else r["empresa"]
                nuevo = odoo.call("purchase.order", "create", cids, vals_list=[{"partner_id": partner_id, "company_id": cid}])
                nuevo = nuevo[0] if isinstance(nuevo, list) else nuevo
                po = odoo.search_read("purchase.order", [("id", "=", nuevo)], ["name", "state", "company_id", "picking_type_id", "origin"], company_ids=cids)[0]
            ftax = "tax_ids" if _tiene_campo("purchase.order.line", "tax_ids", cid) else "taxes_id"
            pls = {x["id"]: x for x in odoo.search_read("purchase.order.line", [("order_id", "=", po["id"]), ("display_type", "=", False)],
                                                         ["product_id", "name", "product_qty", "price_unit", "discount", ftax, "product_uom_id" if _tiene_campo("purchase.order.line", "product_uom_id", cid) else "product_uom"], company_ids=cids)}
            uom_f = "product_uom_id" if _tiene_campo("purchase.order.line", "product_uom_id", cid) else "product_uom"
            # artículos elegidos a mano que ya estaban en el pedido: usar esa línea
            usados = {int(l["po_line"]) for l in lineas if l.get("po_line")}
            for l in lineas:
                if not l.get("po_line"):
                    x = next((k for k, x in pls.items() if k not in usados and x.get("product_id") and x["product_id"][0] == int(l["product_id"])), None)
                    if x:
                        l["po_line"] = x; usados.add(x)
            # 1) lo que no viene -> pedido "Pendiente de Pxxxxx" (antes de quitarlo del original)
            sobran = [x for k, x in pls.items() if k not in usados]
            pendiente = None
            if sobran:
                vals = {"partner_id": partner_id, "company_id": cid, "origin": f"Pendiente de {po['name']}",
                        "order_line": [[0, 0, {"product_id": x["product_id"][0], "name": x["name"], "product_qty": x["product_qty"], "price_unit": x["price_unit"],
                                               "discount": x.get("discount") or 0, ftax: [[6, 0, x.get(ftax) or []]],
                                               uom_f: (x.get(uom_f) or [False])[0]}] for x in sobran if x.get("product_id")]}
                if po.get("picking_type_id"):
                    vals["picking_type_id"] = po["picking_type_id"][0]
                pn = odoo.call("purchase.order", "create", cids, vals_list=[vals])
                pn = pn[0] if isinstance(pn, list) else pn
                pendiente = odoo.search_read("purchase.order", [("id", "=", pn)], ["name"], company_ids=cids)[0]["name"]
            # 2) dejar el pedido igual que el documento
            cmds = [[2, x["id"]] for x in sobran]
            t = _impuesto(cid, 21.0)
            nuevas = []
            for l in lineas:
                v = {"product_qty": _num(l["cantidad"]), "price_unit": _num(l["precio"]), "discount": _num(l.get("descuento"))}
                if l.get("po_line") and int(l["po_line"]) in pls:
                    cmds.append([1, int(l["po_line"]), v])
                else:
                    ti = _impuesto(cid, _num(l.get("iva")) or 21.0) or t
                    v.update({"product_id": int(l["product_id"]), ftax: [[6, 0, [ti] if ti else []]]})
                    cmds.append([0, 0, v]); nuevas.append(l.get("producto") or l.get("descripcion"))
            num = (lec.get("numero") or "").strip() or (r["nombre"] or "")
            odoo.call("purchase.order", "write", cids, ids=[po["id"]], vals={"order_line": cmds, "partner_ref": num})
            # 3) que la base cuadre al céntimo con el documento (ajuste del 4º decimal del precio)
            base_doc = _num(lec.get("base")) - _num(lec.get("portes"))
            pod = odoo.search_read("purchase.order", [("id", "=", po["id"])], ["amount_untaxed", "amount_tax", "amount_total"], company_ids=cids)[0]
            dif = round(base_doc - pod["amount_untaxed"], 2) if base_doc > 0 else 0
            if dif and abs(dif) <= 0.03:
                ls = odoo.search_read("purchase.order.line", [("order_id", "=", po["id"]), ("display_type", "=", False)], ["product_qty", "price_unit", "discount"], company_ids=cids)
                ls = [x for x in ls if x["product_qty"]]
                if ls:
                    x = max(ls, key=lambda x: x["product_qty"])
                    f = x["product_qty"] * (1 - (x.get("discount") or 0) / 100)
                    odoo.call("purchase.order.line", "write", cids, ids=[x["id"]], vals={"price_unit": round(x["price_unit"] + dif / f, 4)})
                    pod = odoo.search_read("purchase.order", [("id", "=", po["id"])], ["amount_untaxed", "amount_tax", "amount_total"], company_ids=cids)[0]
            if base_doc > 0 and abs(pod["amount_untaxed"] - base_doc) > 0.005:
                avisos.append(f"La base del pedido ({pod['amount_untaxed']:.2f} €) no cuadra con la del documento ({base_doc:.2f} €)")
            if _num(lec.get("iva_total")) and abs(pod["amount_tax"] - _num(lec["iva_total"])) > 0.005:
                avisos.append(f"IVA: Odoo {pod['amount_tax']:.2f} € / documento {_num(lec['iva_total']):.2f} €")
            if _num(lec.get("portes")):
                avisos.append(f"El documento lleva {_num(lec['portes']):.2f} € de portes: no se han metido en el pedido")
            # 4) confirmar: crea la entrada para almacén
            odoo.call("purchase.order", "button_confirm", cids, ids=[po["id"]])
            pod = odoo.search_read("purchase.order", [("id", "=", po["id"])], ["name", "state", "picking_ids", "amount_untaxed", "amount_total"], company_ids=cids)[0]
            picks = odoo.search_read("stock.picking", [("id", "in", pod["picking_ids"]), ("state", "not in", ["done", "cancel"])], ["name"], company_ids=cids) if pod["picking_ids"] else []
            poner_pdf("purchase.order", po["id"], r["nombre"] or "documento.pdf", (carpeta / r["fichero"]).read_bytes(), cids)
            # 5) fichas: línea de proveedor con precio, dto. y código; coste si está a 0
            for l in lineas:
                try:
                    tm = odoo.search_read("product.product", [("id", "=", int(l["product_id"]))], ["product_tmpl_id"], company_ids=cids)[0]["product_tmpl_id"][0]
                    sv = {"price": _num(l["precio"]), "discount": _num(l.get("descuento"))}
                    if l.get("ref"):
                        sv["product_code"] = l["ref"]
                    si = odoo.search_read("product.supplierinfo", [("partner_id", "=", partner_id), ("product_tmpl_id", "=", tm), ("company_id", "in", [cid, False])], ["id"], company_ids=cids, limit=1)
                    if si:
                        odoo.call("product.supplierinfo", "write", cids, ids=[si[0]["id"]], vals=sv)
                    else:
                        odoo.call("product.supplierinfo", "create", cids, vals_list=[dict(sv, partner_id=partner_id, product_tmpl_id=tm, company_id=cid)])
                    neto = round(_num(l["precio"]) * (1 - _num(l.get("descuento")) / 100), 4)
                    for c in (1, 4, 5):
                        if c in cids:
                            cp = odoo.search_read("product.product", [("id", "=", int(l["product_id"]))], ["standard_price"], company_ids=[c])
                            if cp and not cp[0]["standard_price"]:
                                odoo.call("product.product", "write", [c], ids=[int(l["product_id"])], vals={"standard_price": neto})
                except Exception as e:
                    avisos.append(f"Ficha de {l.get('producto')}: {str(e)[:120]}")
            if creados:
                avisos.append(f"{len(creados)} artículos nuevos creados: revisad su precio de venta y completad las fichas (botón 📷 Fichas)")
            res = {"pedido": pod["name"], "pedido_id": po["id"], "estado": pod["state"], "recepciones": [p["name"] for p in picks], "creados": creados,
                   "pendiente": pendiente, "base": pod["amount_untaxed"], "total": pod["amount_total"], "anadidas": nuevas,
                   "avisos": avisos, "por": u, "cuando": time.time()}
            if r["odoo_model"] == "account.move" and r["odoo_id"]:
                try:
                    en = enlazar_factura(r["odoo_id"], po["id"], u)
                    res["factura"] = f"Factura enlazada con {en['pedido']}"
                except Exception as e:
                    res["avisos"].append(f"No se pudo enlazar la factura con el pedido: {str(e)[:150]}")
            with lock, con() as c:
                c.execute("UPDATE docs SET compra=? WHERE id=?", (json.dumps(res, ensure_ascii=False), did))
            return res
        return guard(run)

    @app.delete("/api/compras/docs/{did}")
    def borrar_doc(did: str, request: Request):
        current_user(request)
        with lock, con() as c:
            c.execute("UPDATE docs SET estado='descartado' WHERE id=?", (did,))
        return {"ok": True}

    # ---------- recepciones (albaranes) ----------
    @app.get("/api/compras/recepciones")
    def recepciones(request: Request, company: str = "all"):
        _, cids = ctx(request, company)

        def run():
            pk = odoo.search_read("stock.picking", [("picking_type_code", "=", "incoming"), ("state", "in", ["assigned", "confirmed", "waiting"])],
                                  ["name", "partner_id", "origin", "scheduled_date", "backorder_id", "company_id", "purchase_id", "move_ids"],
                                  company_ids=cids, order="scheduled_date asc", limit=200)
            mids = [m for p in pk for m in p.get("move_ids", [])]
            mv = odoo.search_read("stock.move", [("id", "in", mids)], ["picking_id", "product_id", "product_uom_qty", "quantity", "purchase_line_id"],
                                  company_ids=cids) if mids else []
            lines = {}
            for m in mv:
                lines.setdefault(m["picking_id"][0], []).append({"move_id": m["id"], "producto": m["product_id"][1], "product_id": m["product_id"][0],
                                                                 "pedido": m["product_uom_qty"]})
            hoy = dt.date.today()
            docs_po = {}
            with con() as c:
                for d in c.execute("SELECT id, nombre, compra FROM docs WHERE compra IS NOT NULL").fetchall():
                    try:
                        docs_po[json.loads(d["compra"]).get("pedido_id")] = {"id": d["id"], "nombre": d["nombre"]}
                    except Exception:
                        pass
            out = []
            for p in pk:
                fecha = (p.get("scheduled_date") or "")[:10]
                dias = (hoy - dt.date.fromisoformat(fecha)).days if fecha else 0
                out.append({"id": p["id"], "ref": p["name"], "proveedor": (p.get("partner_id") or [0, "—"])[1],
                            "partner_id": (p.get("partner_id") or [0])[0], "origen": p.get("origin") or "",
                            "pedido": (p.get("purchase_id") or [0, ""])[1], "fecha": fecha, "dias": dias,
                            "pendiente_de": (p.get("backorder_id") or [0, ""])[1], "empresa": p["company_id"][1],
                            "lineas": lines.get(p["id"], []), "doc": docs_po.get((p.get("purchase_id") or [0])[0])})
            return out
        return guard(run)

    @app.post("/api/compras/recibir")
    async def recibir(request: Request, company: str = "all"):
        _, cids = ctx(request, company)
        b = await request.json()
        pid = int(b["picking_id"])
        cant = {int(k): float(v or 0) for k, v in (b.get("cantidades") or {}).items()}

        def run():
            pk = odoo.search_read("stock.picking", [("id", "=", pid)], ["company_id", "name", "origin", "move_ids"], company_ids=cids)
            if not pk:
                raise HTTPException(404, "Recepción no encontrada")
            pk = pk[0]
            c1 = [pk["company_id"][0]]
            if not any(v > 0 for v in cant.values()):
                raise HTTPException(400, "No has puesto ninguna cantidad recibida")
            for mid in pk["move_ids"]:
                q = cant.get(mid, 0)
                odoo.call("stock.move", "write", c1, ids=[mid], vals={"quantity": q, "picked": q > 0})
            if b.get("albaran"):
                odoo.call("stock.picking", "write", c1, ids=[pid], vals={"origin": f"{pk.get('origin') or ''} · Albarán {b['albaran']}".strip(" ·")})
            res = odoo.call("stock.picking", "button_validate", c1, ids=[pid])
            pendiente = False
            if isinstance(res, dict) and res.get("res_model") == "stock.backorder.confirmation":
                wctx = dict(res.get("context") or {}, allowed_company_ids=c1)
                w = odoo.call("stock.backorder.confirmation", "create", None, vals_list=[{}], context=wctx)
                w = w[0] if isinstance(w, list) else w
                odoo.call("stock.backorder.confirmation", "process", None, ids=[w], context=wctx)
                pendiente = True
            elif isinstance(res, dict) and res.get("res_model"):
                raise HTTPException(400, f"Odoo pide confirmar algo más ({res.get('name')}). Termínalo en Odoo.")
            # adjuntar el PDF del albarán a la recepción
            if b.get("doc_id"):
                with con() as c:
                    r = c.execute("SELECT fichero, nombre FROM docs WHERE id=?", (b["doc_id"],)).fetchone()
                if r:
                    poner_pdf("stock.picking", pid, r["nombre"] or "albaran.pdf", (carpeta / r["fichero"]).read_bytes(), c1)
                    with lock, con() as c:
                        c.execute("UPDATE docs SET estado='recibido', odoo_model='stock.picking', odoo_id=? WHERE id=?", (pid, b["doc_id"]))
            # qué se puede servir ahora con lo que ha entrado
            prods = {m["product_id"][0] for m in odoo.search_read("stock.move", [("id", "in", pk["move_ids"])], ["product_id"], company_ids=c1)
                     if cant.get(m["id"], 0) > 0}
            servir = []
            try:
                d = odoo.call_kw("sale.order", "get_panel_data", [], company_ids=cids)
                for o in d.get("orders", []):
                    for l in o["lines"]:
                        if l["product_id"] in prods:
                            servir.append({"quien": o["partner_name"], "ref": o["order_name"], "producto": l["product_name"], "pendiente": l["qty_pending"]})
                for p in d.get("reposicion", []):
                    for l in p["lines"]:
                        if l["product_id"] in prods:
                            servir.append({"quien": p["store_name"], "ref": p["picking_name"], "producto": l["product_name"], "pendiente": l["qty_pending"]})
            except Exception:
                pass
            return {"ok": True, "queda_pendiente": pendiente, "servir": servir}
        return guard(run)

    # ---------- facturas para Mayte ----------
    @app.get("/api/compras/facturas")
    def facturas(request: Request, company: str = "all"):
        _, cids = ctx(request, company)

        def run():
            mv = odoo.search_read("account.move", [("move_type", "in", ["in_invoice", "in_refund"]), ("state", "=", "draft")],
                                  ["partner_id", "ref", "invoice_date", "invoice_date_due", "amount_untaxed", "amount_total", "company_id",
                                   "invoice_origin", "extract_state", "create_date", "move_type", "invoice_line_ids"],
                                  company_ids=cids, order="create_date desc", limit=150)
            lids = [l for m in mv for l in m.get("invoice_line_ids", [])]
            ln = odoo.search_read("account.move.line", [("id", "in", lids), ("display_type", "=", "product")],
                                  ["move_id", "product_id", "name", "quantity", "price_unit", "price_subtotal", "purchase_line_id"],
                                  company_ids=cids) if lids else []
            pls = [l["purchase_line_id"][0] for l in ln if l.get("purchase_line_id")]
            pol = {p["id"]: p for p in odoo.search_read("purchase.order.line", [("id", "in", pls)],
                                                        ["order_id", "product_qty", "qty_received", "qty_invoiced", "price_unit"], company_ids=cids)} if pls else {}
            por_factura = {}
            for l in ln:
                por_factura.setdefault(l["move_id"][0], []).append(l)
            # adjuntos (para ver el PDF en Odoo)
            atts = odoo.search_read("ir.attachment", [("res_model", "=", "account.move"), ("res_id", "in", [m["id"] for m in mv]),
                                                      ("mimetype", "=", "application/pdf")], ["res_id"], company_ids=cids) if mv else []
            con_pdf = {a["res_id"]: a["id"] for a in atts}
            with con() as c:
                marcas = {r["move_id"]: {"tipo": r["tipo"], "usuario": r["usuario"], "cuando": r["cuando"], "nota": r["nota"]} for r in c.execute("SELECT * FROM factura_marcas").fetchall()}
            out = []
            for m in mv:
                lineas, avisos, pedidos = [], [], set()
                for l in por_factura.get(m["id"], []):
                    item = {"producto": (l.get("product_id") or [0, l.get("name") or ""])[1], "cantidad": l["quantity"],
                            "precio": l["price_unit"], "importe": l["price_subtotal"]}
                    p = pol.get((l.get("purchase_line_id") or [0])[0])
                    if p:
                        pedidos.add(p["order_id"][1])
                        item["recibido"] = p["qty_received"]
                        item["pedido"] = p["product_qty"]
                        item["precio_pedido"] = p["price_unit"]
                        if l["quantity"] > p["qty_received"] + 1e-6:
                            avisos.append(f"{item['producto'][:40]}: se facturan {l['quantity']:g} y han llegado {p['qty_received']:g}")
                        if abs(l["price_unit"] - p["price_unit"]) > 0.005:
                            avisos.append(f"{item['producto'][:40]}: precio {l['price_unit']:.2f} y en el pedido {p['price_unit']:.2f}")
                    lineas.append(item)
                faltan = []
                if not m.get("partner_id"):
                    faltan.append("proveedor")
                if not m.get("invoice_date"):
                    faltan.append("fecha de factura")
                if not m.get("ref"):
                    faltan.append("nº de factura")
                leyendo = m.get("extract_state") in ("waiting_extraction", "extract_not_ready")
                ls = por_factura.get(m["id"], [])
                if not pedidos:
                    recepcion = "sin_pedido"
                elif any(l["quantity"] > (pol.get((l.get("purchase_line_id") or [0])[0]) or {}).get("qty_received", 0) + 1e-6 for l in ls if l.get("purchase_line_id")):
                    recepcion = "pendiente"
                else:
                    recepcion = "recibido"
                marca = marcas.get(m["id"])
                if marca and marca["tipo"] == "pedido":
                    pn, rec = pedido_de_marca(marca)
                    pedidos.add(pn or "")
                    recepcion = "recibido" if rec else "pendiente"
                    entrada_ok = rec
                else:
                    entrada_ok = recepcion == "recibido" or bool(marca)
                estado = "leyendo" if leyendo else ("diferencias" if avisos or faltan or not entrada_ok else "ok")
                out.append({"id": m["id"], "proveedor": (m.get("partner_id") or [0, "Sin proveedor"])[1], "numero": m.get("ref") or "",
                            "fecha": m.get("invoice_date") or "", "vence": m.get("invoice_date_due") or "",
                            "base": m["amount_untaxed"], "total": m["amount_total"], "empresa": m["company_id"][1],
                            "abono": m["move_type"] == "in_refund", "pedidos": sorted(pedidos) or ([m["invoice_origin"]] if m.get("invoice_origin") else []),
                            "lineas": lineas, "avisos": avisos, "faltan": faltan, "estado": estado, "pdf": con_pdf.get(m["id"]),
                            "sin_pedido": not pedidos, "recepcion": recepcion, "marca": marca, "entrada_ok": entrada_ok})
            return out
        return guard(run)

    @app.post("/api/compras/facturas/{mid}/ok")
    def factura_ok(mid: int, request: Request, company: str = "all"):
        _, cids = ctx(request, company)

        def run():
            m = odoo.search_read("account.move", [("id", "=", mid), ("state", "=", "draft")], ["company_id", "partner_id", "invoice_date"], company_ids=cids)
            if not m:
                raise HTTPException(404, "La factura ya no está en borrador")
            if not m[0].get("partner_id") or not m[0].get("invoice_date"):
                raise HTTPException(400, "Falta el proveedor o la fecha: complétalo en Odoo antes de darle el OK")
            with con() as c:
                marca = c.execute("SELECT tipo, nota FROM factura_marcas WHERE move_id=?", (mid,)).fetchone()
            if marca and marca["tipo"] == "pedido":
                pn, rec = pedido_de_marca(dict(marca))
                if not rec:
                    raise HTTPException(400, f"Almacén todavía no ha recibido todo el pedido {pn}. Si ha llegado, que lo reciban en Recepciones o márcalo como recibido")
            elif not marca:
                ln = odoo.search_read("account.move.line", [("move_id", "=", mid), ("display_type", "=", "product")], ["quantity", "purchase_line_id"], company_ids=cids)
                pls = [l["purchase_line_id"][0] for l in ln if l.get("purchase_line_id")]
                if not pls:
                    raise HTTPException(400, "Esta factura no está cuadrada con ningún pedido: cuádrala con su albarán/pedido, o márcala como gasto o como material recibido")
                rec = {p["id"]: p["qty_received"] for p in odoo.search_read("purchase.order.line", [("id", "in", pls)], ["qty_received"], company_ids=cids)}
                if any(l["quantity"] > rec.get((l.get("purchase_line_id") or [0])[0], 0) + 1e-6 for l in ln if l.get("purchase_line_id")):
                    raise HTTPException(400, "Almacén todavía no ha recibido todo el material de esta factura. Si ha llegado, que lo reciban en Recepciones o márcalo como recibido")
            odoo.call("account.move", "action_post", [m[0]["company_id"][0]], ids=[mid])
            with lock, con() as c:
                c.execute("UPDATE docs SET estado='contabilizada' WHERE odoo_model='account.move' AND odoo_id=?", (mid,))
            return {"ok": True}
        return guard(run)

    @app.post("/api/compras/facturas/{mid}/marcar")
    async def factura_marcar(mid: int, request: Request):
        """tipo: 'gasto' (no lleva entrada de material), 'recibido' (el material llegó sin pasar por compras) o '' para quitar la marca."""
        u, cids = ctx(request, "all")
        b = await request.json()
        tipo = b.get("tipo") or ""
        if tipo not in ("", "gasto", "recibido"):
            raise HTTPException(400, "Marca no válida")
        with lock, con() as c:
            if tipo:
                c.execute("INSERT OR REPLACE INTO factura_marcas VALUES (?,?,?,?,?)", (mid, tipo, u, time.time(), b.get("nota") or ""))
            else:
                c.execute("DELETE FROM factura_marcas WHERE move_id=?", (mid,))
        return {"ok": True}

    @app.get("/api/compras/facturas/{mid}/pedidos")
    def factura_pedidos(mid: int, request: Request):
        """Pedidos de compra del proveedor de la factura que todavía tienen algo sin facturar."""
        _, cids = ctx(request, "all")

        def run():
            m = odoo.search_read("account.move", [("id", "=", mid)], ["partner_id", "ref", "amount_untaxed"], company_ids=cids)
            if not m or not m[0].get("partner_id"):
                raise HTTPException(400, "La factura no tiene proveedor: ponlo en Odoo primero")
            ps = odoo.search_read("purchase.order", [("partner_id", "child_of", m[0]["partner_id"][0]), ("state", "in", ["purchase", "done"]),
                                                     ("invoice_status", "in", ["to invoice", "no"])],
                                  ["name", "date_order", "amount_untaxed", "partner_ref", "receipt_status", "invoice_status", "company_id"], company_ids=TODAS, order="id desc", limit=15)
            return {"factura": {"ref": m[0].get("ref"), "base": m[0]["amount_untaxed"], "proveedor": m[0]["partner_id"][1]},
                    "pedidos": [{"id": p["id"], "name": p["name"], "fecha": (p["date_order"] or "")[:10], "base": p["amount_untaxed"], "ref": p.get("partner_ref") or "",
                                 "recibido": p.get("receipt_status"), "facturable": p.get("invoice_status") == "to invoice",
                                 "empresa": p["company_id"][1],
                                 "coincide": bool(m[0].get("ref") and (p.get("partner_ref") or "").strip().lower() == m[0]["ref"].strip().lower())} for p in ps]}
        return guard(run)

    @app.post("/api/compras/facturas/{mid}/enlazar")
    async def factura_enlazar(mid: int, request: Request):
        """Cuadra la factura con un pedido: las líneas de la factura pasan a ser las del pedido (lo pedido que falte por facturar).
        Si el pedido es de otra empresa (compras en Distriplus, factura en Logipaper) no se tocan las líneas: se apunta el cruce."""
        _u, _cids = ctx(request, "all")
        b = await request.json()
        po_id = int(b.get("pedido_id") or 0)

        return guard(lambda: enlazar_factura(mid, po_id, _u))

    def enlazar_factura(mid, po_id, _u):
        cids = TODAS
        if True:
            m = odoo.search_read("account.move", [("id", "=", mid), ("state", "=", "draft")], ["company_id"], company_ids=cids)
            if not m:
                raise HTTPException(404, "La factura ya no está en borrador")
            pco = odoo.search_read("purchase.order", [("id", "=", po_id)], ["name", "company_id", "amount_untaxed"], company_ids=TODAS)
            if pco and pco[0]["company_id"][0] != m[0]["company_id"][0]:
                # pedido de Distriplus (almacén central) pagado por otra empresa: Odoo no deja enlazarlos, lo apunta el panel
                with lock, con() as c:
                    c.execute("INSERT OR REPLACE INTO factura_marcas VALUES (?,?,?,?,?)",
                              (mid, "pedido", _u, time.time(), json.dumps({"pedido_id": po_id, "pedido": pco[0]["name"], "empresa_pedido": pco[0]["company_id"][0]})))
                t = odoo.search_read("account.move", [("id", "=", mid)], ["amount_untaxed", "amount_total"], company_ids=cids)[0]
                return {"ok": True, "pedido": pco[0]["name"] + " (" + pco[0]["company_id"][1] + ")", "base": t["amount_untaxed"], "total": t["amount_total"],
                        "base_pedido": pco[0]["amount_untaxed"]}
            pol = odoo.search_read("purchase.order.line", [("order_id", "=", po_id), ("display_type", "=", False)],
                                   ["product_id", "name", "product_qty", "qty_invoiced", "qty_received", "price_unit", "discount", "tax_ids"], company_ids=cids)
            lineas = []
            for pl in pol:
                q = round(pl["product_qty"] - pl["qty_invoiced"], 4)
                if q > 0 and pl.get("product_id"):
                    lineas.append([0, 0, {"purchase_line_id": pl["id"], "product_id": pl["product_id"][0], "name": pl["name"], "quantity": q,
                                          "price_unit": pl["price_unit"], "discount": pl.get("discount") or 0, "tax_ids": [[6, 0, pl.get("tax_ids") or []]]}])
            if not lineas:
                raise HTTPException(400, "Ese pedido ya está facturado entero")
            po = odoo.search_read("purchase.order", [("id", "=", po_id)], ["name"], company_ids=cids)[0]
            odoo.call("account.move", "write", cids, ids=[mid], vals={"invoice_line_ids": [[5, 0, 0]] + lineas, "invoice_origin": po["name"]},
                      context={"default_move_type": "in_invoice"})
            t = odoo.search_read("account.move", [("id", "=", mid)], ["amount_untaxed", "amount_total"], company_ids=cids)[0]
            return {"ok": True, "pedido": po["name"], "base": t["amount_untaxed"], "total": t["amount_total"]}

    @app.get("/api/compras/bandeja")
    def bandeja(request: Request, company: str = "all", dias: int = 45):
        """Todo lo subido con su lectura y en qué paso está. Se queda como pendiente hasta que se termina:
        albarán -> cuadrado con su pedido y recibido por almacén; factura -> contabilizada (con la entrada confirmada)."""
        _, cids = ctx(request, company)
        desde = time.time() - dias * 86400
        with con() as c:
            ds = [dict(r) for r in c.execute("SELECT * FROM docs WHERE estado != 'descartado' AND subido >= ? AND empresa IN (%s) ORDER BY subido DESC"
                                              % ",".join("?" * len(cids)), [desde] + list(cids)).fetchall()]
            marcas = {r["move_id"]: {"tipo": r["tipo"], "usuario": r["usuario"], "nota": r["nota"]} for r in c.execute("SELECT * FROM factura_marcas").fetchall()}

        def run():
            for d in ds:
                d["lectura"] = json.loads(d["lectura"]) if d.get("lectura") else None
                d["compra"] = json.loads(d["compra"]) if d.get("compra") else None
                if d["lectura"] is None and d["fichero"]:
                    leer_en_segundo_plano(d["id"])
            # albaranes ya metidos por otra vía: pedido confirmado con el nº del documento como ref. del proveedor
            nums = {(d["lectura"] or {}).get("numero", "").strip(): d for d in ds if d["lectura"] and not d["compra"] and (d["lectura"].get("numero") or "").strip()}
            if nums:
                pos = odoo.search_read("purchase.order", [("partner_ref", "in", list(nums)), ("state", "in", ["purchase", "done"])], ["name", "partner_ref", "picking_ids"], company_ids=cids)
                for po in pos:
                    d = nums.get((po.get("partner_ref") or "").strip())
                    if d and d["tipo"] == "albaran":
                        d["compra"] = {"pedido": po["name"], "pedido_id": po["id"], "ya_estaba": True}
                        with lock, con() as c:
                            c.execute("UPDATE docs SET compra=? WHERE id=?", (json.dumps(d["compra"], ensure_ascii=False), d["id"]))
            # estado de las entradas de los pedidos
            po_ids = [d["compra"]["pedido_id"] for d in ds if d["compra"] and d["compra"].get("pedido_id")]
            pick = {}
            if po_ids:
                for pk in odoo.search_read("stock.picking", [("purchase_id", "in", po_ids), ("picking_type_code", "=", "incoming")], ["name", "state", "purchase_id"], company_ids=cids):
                    pick.setdefault(pk["purchase_id"][0], []).append(pk)
            # facturas en Odoo
            mids = [d["odoo_id"] for d in ds if d["odoo_model"] == "account.move" and d["odoo_id"]]
            FM = ["state", "name", "partner_id", "ref", "amount_total", "amount_untaxed", "invoice_date", "invoice_origin", "move_type"]
            mv = {m["id"]: m for m in odoo.search_read("account.move", [("id", "in", mids)], FM, company_ids=cids)} if mids else {}
            # factura borrada y rehecha en Odoo: volver a enlazarla por su nº
            for d in ds:
                if d["odoo_model"] == "account.move" and d["odoo_id"] and d["odoo_id"] not in mv and (d["lectura"] or {}).get("numero"):
                    otra = odoo.search_read("account.move", [("ref", "=ilike", d["lectura"]["numero"].strip()), ("move_type", "in", ["in_invoice", "in_refund"]),
                                                             ("state", "!=", "cancel")], FM, company_ids=cids, limit=1)
                    if otra:
                        mv[otra[0]["id"]] = otra[0]
                        d["odoo_id"] = otra[0]["id"]; mids.append(otra[0]["id"])
                        with lock, con() as c:
                            c.execute("UPDATE docs SET odoo_id=? WHERE id=?", (otra[0]["id"], d["id"]))
            ln = odoo.search_read("account.move.line", [("move_id", "in", mids), ("display_type", "=", "product")], ["move_id", "quantity", "purchase_line_id"], company_ids=cids) if mids else []
            pls = [l["purchase_line_id"][0] for l in ln if l.get("purchase_line_id")]
            pol = {p["id"]: p for p in odoo.search_read("purchase.order.line", [("id", "in", pls)], ["qty_received", "order_id"], company_ids=cids)} if pls else {}
            por_mv = {}
            for l in ln:
                por_mv.setdefault(l["move_id"][0], []).append(l)
            for d in ds:
                if d["odoo_model"] == "account.move" and d["odoo_id"] in mv and d["odoo_id"] not in REPARADAS:
                    REPARADAS.add(d["odoo_id"])
                    try:
                        if not arreglar_pdf_principal("account.move", d["odoo_id"], TODAS):
                            m_ = odoo.search_read("account.move", [("id", "=", d["odoo_id"])], ["message_main_attachment_id"], company_ids=TODAS)
                            if m_ and not m_[0].get("message_main_attachment_id") and d.get("fichero") and (carpeta / d["fichero"]).exists():
                                poner_pdf("account.move", d["odoo_id"], d["nombre"], (carpeta / d["fichero"]).read_bytes(), TODAS)
                    except Exception:
                        pass
            out = []
            for d in ds:
                lec = d["lectura"] or {}
                e = {"id": d["id"], "tipo": d["tipo"], "nombre": d["nombre"], "usuario": d["usuario"], "subido": d["subido"], "empresa": d["empresa"],
                     "proveedor": lec.get("proveedor") or "", "numero": lec.get("numero") or "", "fecha": lec.get("fecha") or "", "total": lec.get("total"),
                     "base": lec.get("base"), "tipo_leido": lec.get("tipo") or "", "odoo_id": d["odoo_id"], "compra": d["compra"], "acciones": []}
                if d["estado"] == "error":
                    e.update(paso="error", texto=d.get("nota") or "No se pudo subir a Odoo", acciones=["descartar"])
                elif d["lectura"] is None:
                    e.update(paso="leyendo", texto="Leyendo el PDF…")
                elif lec.get("error") and d["tipo"] == "albaran":
                    e.update(paso="error", texto="No he podido leer el PDF: " + lec["error"][:150], acciones=["releer", "descartar"])
                elif d["tipo"] == "albaran":
                    if not d["compra"]:
                        e.update(paso="pendiente", texto="Falta cuadrarlo con su pedido de compra", acciones=["cuadrar", "descartar"])
                    else:
                        pk = pick.get(d["compra"].get("pedido_id"), [])
                        abiertas = [x["name"] for x in pk if x["state"] not in ("done", "cancel")]
                        if pk and not abiertas:
                            e.update(paso="terminado", texto=f"Cuadrado con {d['compra']['pedido']} y recibido por almacén")
                        else:
                            e.update(paso="espera", texto=f"Cuadrado con {d['compra']['pedido']}. Falta que almacén reciba la entrada {', '.join(abiertas) or ''} en Recepciones",
                                     acciones=["fichas"])
                else:
                    m = mv.get(d["odoo_id"])
                    if not m or m["state"] == "cancel":
                        e.update(paso="error", texto="La factura ya no está en Odoo (borrada o cancelada)", acciones=["descartar"])
                    elif m["state"] == "posted":
                        e.update(paso="terminado", texto=f"Contabilizada {m['name']}" + (f" · pedido {m['invoice_origin']}" if m.get("invoice_origin") else ""))
                    else:
                        ls = por_mv.get(m["id"], [])
                        con_po = [l for l in ls if l.get("purchase_line_id")]
                        pedidos = sorted({pol[l["purchase_line_id"][0]]["order_id"][1] for l in con_po if l["purchase_line_id"][0] in pol})
                        marca = marcas.get(m["id"])
                        e["marca"] = marca
                        e["pedidos"] = pedidos
                        if marca and marca["tipo"] == "pedido":
                            pn, rec = pedido_de_marca(marca)
                            if rec:
                                e.update(paso="ok", texto=f"Cruzada con el pedido {pn} del almacén central y material recibido. Lista para el OK de Mayte", acciones=["ok", "quitar_marca"])
                            else:
                                e.update(paso="espera", texto=f"Cruzada con el pedido {pn} del almacén central. Falta que almacén lo reciba", acciones=["recibido", "quitar_marca"])
                        elif marca:
                            e.update(paso="ok", texto=("Gasto sin entrada de material" if marca["tipo"] == "gasto" else "Material marcado como recibido") + f" ({marca['usuario']}). Lista para el OK de Mayte",
                                     acciones=["ok", "quitar_marca"])
                        elif not con_po:
                            e.update(paso="pendiente", texto="Sin pedido de compra: cuádrala desde el PDF (crea el pedido y la entrada para almacén), enlázala con un pedido que ya exista, o márcala como gasto / material recibido",
                                     acciones=["cuadrar", "cuadrar_factura", "gasto", "recibido"])
                        elif any(l["quantity"] > pol.get(l["purchase_line_id"][0], {}).get("qty_received", 0) + 1e-6 for l in con_po):
                            e.update(paso="espera", texto=f"Cuadrada con {', '.join(pedidos)}. Falta que almacén reciba el material", acciones=["recibido"])
                        else:
                            e.update(paso="ok", texto=f"Cuadrada con {', '.join(pedidos)} y material recibido. Lista para el OK de Mayte", acciones=["ok"])
                        if _num(lec.get("total")) < 0 and m.get("move_type") == "in_invoice":
                            e["texto"] += " · ⚠️ El PDF es un ABONO (importe negativo) pero en Odoo está como factura: hay que pasarla a factura rectificativa"
                        if not m.get("invoice_date") or not m.get("partner_id"):
                            e["texto"] += " · en Odoo falta " + ", ".join(x for x, v in (("proveedor", m.get("partner_id")), ("fecha", m.get("invoice_date"))) if not v)
                    if m:
                        e["odoo_total"] = m["amount_total"]
                        e["proveedor"] = e["proveedor"] or (m.get("partner_id") or [0, ""])[1]
                out.append(e)
            return out
        return guard(run)

    @app.post("/api/compras/docs/{did}/releer")
    def doc_releer(did: str, request: Request):
        ctx(request, "all")
        with lock, con() as c:
            c.execute("UPDATE docs SET lectura=NULL WHERE id=?", (did,))
        leer_en_segundo_plano(did)
        return {"ok": True}

    # ---------- facturas recurrentes ----------
    @app.get("/api/compras/recurrentes")
    def recurrentes(request: Request, mes: str = "", company: str = "all"):
        _, cids = ctx(request, company)
        mes = mes or dt.date.today().strftime("%Y-%m")
        y, m = map(int, mes.split("-"))
        ini = dt.date(y, m, 1)
        fin = (ini + dt.timedelta(days=32)).replace(day=1)
        with con() as c:
            rs = [dict(r) for r in c.execute("SELECT * FROM recurrentes WHERE activo=1 AND empresa IN (%s) ORDER BY dia, partner_nombre"
                                              % ",".join("?" * len(cids)), cids).fetchall()]
            marcas = {r["rid"]: r["nota"] for r in c.execute("SELECT * FROM recurrentes_marcas WHERE mes=?", (mes,)).fetchall()}

        def run():
            pids = list({r["partner_id"] for r in rs if r["partner_id"]})
            mv = odoo.search_read("account.move", [("move_type", "=", "in_invoice"), ("state", "!=", "cancel"), ("partner_id", "in", pids),
                                                   "|", "&", ("invoice_date", ">=", ini.isoformat()), ("invoice_date", "<", fin.isoformat()),
                                                   "&", ("invoice_date", "=", False), ("create_date", ">=", ini.isoformat())],
                                  ["partner_id", "ref", "invoice_date", "amount_total", "company_id", "state"], company_ids=cids) if pids else []
            usados = set()
            hoy = dt.date.today()
            for r in rs:
                f = next((x for x in mv if x["partner_id"][0] == r["partner_id"] and x["company_id"][0] == r["empresa"] and x["id"] not in usados), None)
                if f:
                    usados.add(f["id"])
                    r.update(llegada=True, factura={"id": f["id"], "numero": f.get("ref") or "", "fecha": f.get("invoice_date") or "",
                                                    "total": f["amount_total"], "borrador": f["state"] == "draft"})
                elif r["id"] in marcas:
                    r.update(llegada=True, manual=marcas[r["id"]] or "Marcada a mano")
                else:
                    r["llegada"] = False
                    r["retraso"] = ini.year == hoy.year and ini.month == hoy.month and hoy.day > (r["dia"] or 31) or ini < hoy.replace(day=1)
            return {"mes": mes, "lista": rs}
        return guard(run)

    @app.post("/api/compras/recurrentes")
    async def recurrente_guardar(request: Request):
        u, cids = ctx(request)
        b = await request.json()
        emp = int(b.get("empresa") or cids[0])
        if emp not in cids:
            raise HTTPException(403, "Empresa no permitida")
        vals = (emp, b.get("empresa_nombre") or "", int(b["partner_id"]) if b.get("partner_id") else None, b.get("partner_nombre") or "",
                b.get("concepto") or "", float(b.get("importe") or 0), int(b.get("dia") or 0))
        with lock, con() as c:
            if b.get("id"):
                c.execute("UPDATE recurrentes SET empresa=?, empresa_nombre=?, partner_id=?, partner_nombre=?, concepto=?, importe=?, dia=? WHERE id=?",
                          vals + (int(b["id"]),))
            else:
                c.execute("INSERT INTO recurrentes (empresa, empresa_nombre, partner_id, partner_nombre, concepto, importe, dia) VALUES (?,?,?,?,?,?,?)", vals)
        return {"ok": True}

    @app.delete("/api/compras/recurrentes/{rid}")
    def recurrente_borrar(rid: int, request: Request):
        current_user(request)
        with lock, con() as c:
            c.execute("UPDATE recurrentes SET activo=0 WHERE id=?", (rid,))
        return {"ok": True}

    @app.post("/api/compras/recurrentes/{rid}/marca")
    async def recurrente_marca(rid: int, request: Request):
        current_user(request)
        b = await request.json()
        with lock, con() as c:
            if b.get("quitar"):
                c.execute("DELETE FROM recurrentes_marcas WHERE rid=? AND mes=?", (rid, b["mes"]))
            else:
                c.execute("INSERT OR REPLACE INTO recurrentes_marcas VALUES (?,?,?)", (rid, b["mes"], b.get("nota") or ""))
        return {"ok": True}

    @app.get("/api/compras/proveedores")
    def proveedores(request: Request, q: str = "", company: str = "all"):
        _, cids = ctx(request, company)
        if len(q.strip()) < 2:
            return []
        return guard(lambda: odoo.search_read("res.partner", ["|", ("name", "ilike", q), ("vat", "ilike", q)], ["display_name", "vat"],
                                              company_ids=cids, limit=12))


def setup_fotos(app, *, odoo, current_user, scope, guard):
    """Subir el Excel de un proveedor con fotos incrustadas (como el de Depesche) y ponerlas en los artículos por EAN."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    from fastapi.responses import HTMLResponse

    NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
          "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}

    def leer_excel(datos):
        z = zipfile.ZipFile(io.BytesIO(datos))
        ss = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
                ss.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        filas = {}
        for row in sheet.iter("{%s}row" % NS["m"]):
            n = int(row.get("r"))
            for c in row.findall("m:c", NS):
                col = re.match(r"[A-Z]+", c.get("r")).group(0)
                v = c.find("m:v", NS)
                val = (v.text if v is not None else "") or ""
                if c.get("t") == "s" and val:
                    val = ss[int(val)]
                elif c.get("t") == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter("{%s}t" % NS["m"]))
                filas.setdefault(n, {})[col] = val.strip()
        cab = filas.get(1, {})
        col_ean = next((k for k, v in cab.items() if "EAN" in v.upper() or "BARRAS" in v.upper()), None)
        col_ref = next((k for k, v in cab.items() if v.upper().replace(".", "") in ("ART NO", "ARTNO", "REF", "REFERENCIA", "CÓDIGO", "CODIGO")), None)
        # imágenes y su fila
        rels = ET.fromstring(z.read("xl/drawings/_rels/drawing1.xml.rels"))
        rid2f = {r.get("Id"): "xl/drawings/" + r.get("Target") for r in rels}
        dr = ET.fromstring(z.read("xl/drawings/drawing1.xml"))
        fotos = []
        for anc in list(dr):
            fr = anc.find("xdr:from", NS)
            blip = anc.find(".//a:blip", NS)
            if fr is None or blip is None:
                continue
            fila = int(fr.find("xdr:row", NS).text) + 1
            f = rid2f.get(blip.get("{%s}embed" % NS["r"]), "")
            f = f.replace("xl/drawings/../", "xl/")
            if f in z.namelist():
                fotos.append((fila, z.read(f)))
        return [{"fila": fi, "ean": filas.get(fi, {}).get(col_ean, "") if col_ean else "", "ref": filas.get(fi, {}).get(col_ref, "") if col_ref else "",
                 "foto": b} for fi, b in fotos], bool(col_ean)

    @app.post("/api/compras/fotos_excel")
    async def fotos_excel(request: Request, archivo: UploadFile = File(...), sobrescribir: str = Form("no")):
        u = current_user(request)
        cids, _ = scope(u, "all")
        datos = await archivo.read()

        def run():
            try:
                items, hay_ean = leer_excel(datos)
            except Exception as e:
                raise HTTPException(400, f"No he podido leer el Excel: {e}")
            if not hay_ean:
                raise HTTPException(400, "El Excel no tiene una columna EAN / código de barras")
            out = {"fotos": len(items), "puestas": 0, "ya_tenian": 0, "no_encontrados": []}
            for it in items:
                ean = re.sub(r"\D", "", it["ean"])
                if not ean:
                    continue
                p = odoo.search_read("product.product", ["|", ("barcode", "=", ean), ("barcode", "=", "0" + ean)],
                                     ["product_tmpl_id", "image_128"], company_ids=cids, limit=1)
                if not p:
                    out["no_encontrados"].append(it["ref"] or ean)
                    continue
                if p[0].get("image_128") and sobrescribir != "si":
                    out["ya_tenian"] += 1
                    continue
                odoo.call("product.template", "write", cids, ids=[p[0]["product_tmpl_id"][0]], vals={"image_1920": base64.b64encode(it["foto"]).decode()})
                out["puestas"] += 1
            return out
        return guard(run)

    @app.get("/compras/fotos")
    def fotos_pagina(request: Request):
        current_user(request)
        return HTMLResponse("""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Fotos desde Excel</title>
<style>body{font:15px -apple-system,Segoe UI,sans-serif;background:#3A1F73;display:grid;place-items:center;min-height:100vh;margin:0}
.c{background:#fff;border-radius:14px;padding:28px;max-width:460px;display:flex;flex-direction:column;gap:12px}h2{margin:0;color:#3A1F73}
button{background:#F39A2B;color:#fff;border:0;border-radius:8px;padding:11px;font-weight:700;font-size:15px;cursor:pointer}#r{font-weight:600;white-space:pre-wrap}</style>
<div class=c><h2>Fotos de artículos desde Excel</h2><p>Sube el Excel del proveedor que trae las fotos dentro (por ejemplo el de Depesche). Cada foto se pone en el artículo de Odoo con el mismo código de barras (EAN).</p>
<input type=file id=f accept=".xlsx"><label><input type=checkbox id=s> Cambiar también las fotos que ya tengan</label><button id=b>Poner fotos</button><p id=r></p><a href="/#compras">← Volver al panel</a></div>
<script>b.onclick=async()=>{if(!f.files[0])return r.textContent='Elige el Excel';r.textContent='Subiendo y poniendo fotos…';const fd=new FormData();fd.append('archivo',f.files[0]);fd.append('sobrescribir',s.checked?'si':'no');
const x=await fetch('/api/compras/fotos_excel',{method:'POST',body:fd});const j=await x.json();
r.textContent=x.ok?`Hecho: ${j.puestas} fotos puestas de ${j.fotos}.`+(j.ya_tenian?`\\n${j.ya_tenian} ya tenían foto (no tocadas).`:'')+(j.no_encontrados.length?`\\nNo están en Odoo: ${j.no_encontrados.join(', ')}`:''):'Error: '+(j.detail||x.status)}</script>""")
