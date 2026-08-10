"""core/sync/providers/odoo.py — extrae partners, productos y facturas de Odoo."""

import httpx


def _jsonrpc(credentials: dict, service: str, method: str, args: list = None, kwargs: dict = None) -> any:
    url = credentials.get("url", "").rstrip("/")
    db = credentials.get("database")
    username = credentials.get("username")
    password = credentials.get("api_key")
    if not all([url, db, username, password]):
        raise ValueError("Faltan credenciales de Odoo")

    if service == "common":
        payload_args = args
    else:
        uid = credentials.get("_uid")
        if not uid:
            auth = httpx.post(
                f"{url}/jsonrpc",
                json={
                    "jsonrpc": "2.0",
                    "method": "call",
                    "params": {
                        "service": "common",
                        "method": "authenticate",
                        "args": [db, username, password, {}],
                    },
                    "id": 1,
                },
                headers={"Content-Type": "application/json"},
                timeout=30,
                follow_redirects=False,
            )
            auth.raise_for_status()
            auth_data = auth.json()
            if auth_data.get("error"):
                raise ValueError(auth_data["error"].get("message", "Error Odoo"))
            uid = auth_data["result"]
            credentials["_uid"] = uid
        payload_args = [db, uid, password] + (args or [])

    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "service": service,
            "method": method,
            "args": payload_args,
            "kwargs": kwargs or {},
        },
        "id": 1,
    }
    r = httpx.post(
        f"{url}/jsonrpc",
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=60,
        follow_redirects=False,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise ValueError(data["error"].get("message", "Error Odoo"))
    return data["result"]


def _search_read(credentials, model, domain=None, fields=None, limit=500) -> list[dict]:
    return _jsonrpc(
        credentials,
        "object",
        "execute_kw",
        args=[model, "search_read"],
        kwargs={"domain": domain or [], "fields": fields or ["id"], "limit": limit},
    )


def sync(credentials: dict) -> dict:
    # Autenticar y guardar uid en el dict
    _jsonrpc(credentials, "common", "authenticate", args=[credentials["database"], credentials["username"], credentials["api_key"], {}])

    # Partners (clientes/proveedores)
    partners = _search_read(
        credentials,
        "res.partner",
        domain=["|", ("customer_rank", ">", 0), ("supplier_rank", ">", 0)],
        fields=["id", "name", "is_company", "email", "phone", "street", "city", "country_id", "vat", "parent_id"],
        limit=500,
    )
    companies = []
    contacts = []
    for p in partners:
        ext_id = str(p["id"])
        country = None
        if p.get("country_id") and isinstance(p["country_id"], (list, tuple)) and len(p["country_id"]) > 1:
            country = p["country_id"][1]
        if p.get("is_company"):
            companies.append({
                "external_id": ext_id,
                "name": p.get("name") or "Sin nombre",
                "website": None,
                "phone": p.get("phone"),
                "email": p.get("email"),
                "address": p.get("street"),
                "city": p.get("city"),
                "country": country or "MX",
                "industry": None,
                "tax_id": p.get("vat"),
            })
        else:
            parent_id = None
            if p.get("parent_id") and isinstance(p["parent_id"], (list, tuple)) and len(p["parent_id"]) > 0:
                parent_id = str(p["parent_id"][0])
            contacts.append({
                "external_id": ext_id,
                "first_name": p.get("name") or "Sin nombre",
                "last_name": None,
                "email": p.get("email"),
                "phone": p.get("phone"),
                "job_title": None,
                "company_external_id": parent_id,
            })

    # Productos
    products_raw = _search_read(
        credentials,
        "product.product",
        domain=[("type", "in", ["product", "consu"])],
        fields=["id", "name", "default_code", "list_price", "standard_price", "qty_available", "uom_id"],
        limit=500,
    )
    products = []
    for pr in products_raw:
        ext_id = str(pr["id"])
        unit = None
        if pr.get("uom_id") and isinstance(pr["uom_id"], (list, tuple)) and len(pr["uom_id"]) > 1:
            unit = pr["uom_id"][1]
        products.append({
            "external_id": ext_id,
            "name": pr.get("name") or "Producto sin nombre",
            "sku": pr.get("default_code") or ext_id,
            "description": None,
            "price": float(pr.get("list_price") or 0),
            "currency": "MXN",
            "stock": float(pr.get("qty_available") or 0),
            "unit": unit,
        })

    # Facturas de cliente
    invoices_raw = _search_read(
        credentials,
        "account.move",
        domain=[("move_type", "=", "out_invoice")],
        fields=["id", "name", "invoice_date", "invoice_date_due", "partner_id", "amount_total", "currency_id", "state", "invoice_line_ids"],
        limit=500,
    )
    invoices = []
    for inv in invoices_raw:
        ext_id = str(inv["id"])
        partner_ext = None
        if inv.get("partner_id") and isinstance(inv["partner_id"], (list, tuple)) and len(inv["partner_id"]) > 0:
            partner_ext = str(inv["partner_id"][0])
        currency = "MXN"
        if inv.get("currency_id") and isinstance(inv["currency_id"], (list, tuple)) and len(inv["currency_id"]) > 1:
            currency = inv["currency_id"][1]
        status_map = {
            "draft": "draft",
            "posted": "sent",
            "cancel": "cancelled",
        }
        items = []
        if inv.get("invoice_line_ids"):
            lines = _search_read(
                credentials,
                "account.move.line",
                domain=[("id", "in", inv["invoice_line_ids"])],
                fields=["id", "name", "quantity", "price_unit", "product_id"],
                limit=500,
            )
            for line in lines:
                product_ext = None
                if line.get("product_id") and isinstance(line["product_id"], (list, tuple)) and len(line["product_id"]) > 0:
                    product_ext = str(line["product_id"][0])
                items.append({
                    "description": line.get("name") or "Concepto",
                    "quantity": float(line.get("quantity") or 1),
                    "unit_price": float(line.get("price_unit") or 0),
                    "external_product_id": product_ext,
                })
        invoices.append({
            "external_id": ext_id,
            "invoice_number": inv.get("name") or ext_id,
            "contact_external_id": partner_ext,
            "company_external_id": partner_ext,
            "status": status_map.get(inv.get("state"), "draft"),
            "issue_date": inv.get("invoice_date"),
            "due_date": inv.get("invoice_date_due"),
            "total": float(inv.get("amount_total") or 0),
            "currency": currency,
            "items": items,
        })

    return {
        "companies": companies,
        "contacts": contacts,
        "deals": [],
        "products": products,
        "invoices": invoices,
    }
