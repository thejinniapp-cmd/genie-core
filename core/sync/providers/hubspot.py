"""core/sync/providers/hubspot.py — extrae contactos, empresas y deals de HubSpot."""

import httpx


def _paginated_get(url: str, headers: dict, params: dict) -> list[dict]:
    results = []
    while True:
        r = httpx.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        paging = data.get("paging", {})
        next_info = paging.get("next")
        if not next_info or not next_info.get("after"):
            break
        params["after"] = next_info["after"]
    return results


def sync(credentials: dict) -> dict:
    token = credentials.get("access_token", "").strip()
    if not token:
        raise ValueError("Falta access_token de HubSpot")

    headers = {"Authorization": f"Bearer {token}"}
    base = "https://api.hubapi.com/crm/v3/objects"

    # ---------- Contactos ----------
    contacts_raw = _paginated_get(
        f"{base}/contacts",
        headers,
        {
            "properties": "firstname,lastname,email,phone,jobtitle",
            "limit": 100,
        },
    )
    hubspot_contact_to_ext = {}
    contacts = []
    for item in contacts_raw:
        props = item.get("properties", {})
        ext_id = str(item.get("id"))
        hubspot_contact_to_ext[item["id"]] = ext_id
        first = props.get("firstname", "")
        last = props.get("lastname", "")
        contacts.append({
            "external_id": ext_id,
            "first_name": first,
            "last_name": last,
            "email": props.get("email"),
            "phone": props.get("phone"),
            "job_title": props.get("jobtitle"),
            "company_external_id": None,
        })

    # ---------- Empresas ----------
    companies_raw = _paginated_get(
        f"{base}/companies",
        headers,
        {
            "properties": "name,website,phone,address,industry",
            "limit": 100,
        },
    )
    hubspot_company_to_ext = {}
    companies = []
    for item in companies_raw:
        props = item.get("properties", {})
        ext_id = str(item.get("id"))
        hubspot_company_to_ext[item["id"]] = ext_id
        companies.append({
            "external_id": ext_id,
            "name": props.get("name", "Sin nombre"),
            "website": props.get("website"),
            "phone": props.get("phone"),
            "email": None,
            "address": props.get("address"),
            "city": None,
            "country": "MX",
            "industry": props.get("industry"),
            "tax_id": None,
        })

    # ---------- Deals ----------
    deals_raw = _paginated_get(
        f"{base}/deals",
        headers,
        {
            "properties": "dealname,amount,closedate,dealstage,pipeline,hs_is_closed_won,hs_is_closed",
            "associations": "contacts,companies",
            "limit": 100,
        },
    )
    deals = []
    for item in deals_raw:
        props = item.get("properties", {})
        ext_id = str(item.get("id"))
        status = "open"
        if props.get("hs_is_closed_won") == "true" or props.get("hs_is_closed_won") is True:
            status = "won"
        elif props.get("hs_is_closed") == "true" or props.get("hs_is_closed") is True:
            status = "lost"

        assoc = item.get("associations", {})
        contact_ext = None
        company_ext = None
        for assoc_type, assoc_data in assoc.items():
            for res in assoc_data.get("results", []):
                hid = res.get("id")
                if assoc_type.startswith("contacts") and hid in hubspot_contact_to_ext:
                    contact_ext = hubspot_contact_to_ext[hid]
                elif assoc_type.startswith("companies") and hid in hubspot_company_to_ext:
                    company_ext = hubspot_company_to_ext[hid]

        deals.append({
            "external_id": ext_id,
            "name": props.get("dealname", "Deal sin nombre"),
            "value": float(props.get("amount") or 0),
            "currency": "USD",
            "status": status,
            "expected_close_date": props.get("closedate") or None,
            "company_external_id": company_ext,
            "contact_external_id": contact_ext,
            "stage_name": props.get("dealstage") or "N/A",
            "pipeline_name": props.get("pipeline") or "default",
        })

    return {
        "companies": companies,
        "contacts": contacts,
        "deals": deals,
        "products": [],
        "invoices": [],
    }
