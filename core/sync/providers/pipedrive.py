"""core/sync/providers/pipedrive.py — extrae personas, empresas, deals y productos."""

import httpx


def _get(endpoint: str, token: str) -> list[dict]:
    url = f"https://api.pipedrive.com/v1/{endpoint}"
    params = {"api_token": token, "limit": 100}
    results = []
    start = 0
    while True:
        params["start"] = start
        r = httpx.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise ValueError(data.get("error") or "Error de Pipedrive")
        results.extend(data.get("data") or [])
        pagination = data.get("pagination", {})
        if not pagination.get("more_items_in_collection"):
            break
        start = pagination.get("next_start", start + len(data.get("data") or []))
    return results


def _first_value(field) -> str | None:
    if isinstance(field, list) and field:
        return field[0].get("value") if isinstance(field[0], dict) else field[0]
    if isinstance(field, dict):
        return field.get("value")
    return field


def _split_name(name: str) -> tuple[str, str]:
    parts = (name or "").strip().split(" ", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def sync(credentials: dict) -> dict:
    token = credentials.get("api_token", "").strip()
    if not token:
        raise ValueError("Falta api_token de Pipedrive")

    # Organizaciones
    orgs_raw = _get("organizations", token)
    companies = []
    for o in orgs_raw:
        ext_id = str(o.get("id"))
        address = o.get("address")
        address_str = address.get("formatted_address") if isinstance(address, dict) else str(address) if address else None
        companies.append({
            "external_id": ext_id,
            "name": o.get("name") or "Sin nombre",
            "website": o.get("website") or None,
            "phone": _first_value(o.get("phone")),
            "email": _first_value(o.get("email")),
            "address": address_str,
            "city": None,
            "country": "MX",
            "industry": None,
            "tax_id": None,
        })

    # Personas
    persons_raw = _get("persons", token)
    contacts = []
    for p in persons_raw:
        ext_id = str(p.get("id"))
        first, last = _split_name(p.get("name"))
        contacts.append({
            "external_id": ext_id,
            "first_name": first,
            "last_name": last,
            "email": _first_value(p.get("email")),
            "phone": _first_value(p.get("phone")),
            "job_title": None,
            "company_external_id": str(p.get("org_id")) if p.get("org_id") else None,
        })

    # Deals
    deals_raw = _get("deals", token)
    deals = []
    for d in deals_raw:
        ext_id = str(d.get("id"))
        deals.append({
            "external_id": ext_id,
            "name": d.get("title") or "Deal sin nombre",
            "value": float(d.get("value") or 0),
            "currency": d.get("currency") or "USD",
            "status": d.get("status") or "open",
            "expected_close_date": d.get("expected_close_date") or None,
            "company_external_id": str(d.get("org_id")) if d.get("org_id") else None,
            "contact_external_id": str(d.get("person_id")) if d.get("person_id") else None,
            "stage_name": str(d.get("stage_id")) if d.get("stage_id") else "N/A",
            "pipeline_name": str(d.get("pipeline_id")) if d.get("pipeline_id") else "default",
        })

    # Productos
    products_raw = _get("products", token)
    products = []
    for pr in products_raw:
        ext_id = str(pr.get("id"))
        prices = pr.get("prices") or []
        first_price = prices[0] if prices else {}
        products.append({
            "external_id": ext_id,
            "name": pr.get("name") or "Producto sin nombre",
            "sku": pr.get("code") or ext_id,
            "description": None,
            "price": float(first_price.get("price") or 0) if first_price else 0,
            "currency": (first_price.get("currency") if first_price else None) or "USD",
            "stock": 0,
        })

    return {
        "companies": companies,
        "contacts": contacts,
        "deals": deals,
        "products": products,
        "invoices": [],
    }
