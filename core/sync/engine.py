"""
core/sync/engine.py
===================
Motor de sincronización funcional para conectores externos.

Trae datos de HubSpot, Pipedrive y Odoo hacia las tablas internas de Genie
(CRM, inventario, facturación) para que agentes, flujos y el dashboard
puedan usar la información como una entidad más del sistema.

Relaciones clave preservadas:
- deals -> companies / contacts
- contacts -> companies
- invoices -> companies / contacts
- inventory items -> products
"""

import os
import logging
from datetime import datetime, timezone
from supabase import create_client

from .providers import hubspot, pipedrive, odoo

log = logging.getLogger("genie.sync")


PROVIDERS = {
    "hubspot": hubspot.sync,
    "pipedrive": pipedrive.sync,
    "odoo": odoo.sync,
}


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _audit_start(org_id: str, connector_type: str) -> str:
    res = _db().table("audit_log").insert({
        "org_id": org_id,
        "action": "connector_sync",
        "actor_type": "system",
        "input_data": {"connector_type": connector_type},
        "status": "pending",
        "metadata": {
            "connector_type": connector_type,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    }).execute()
    return res.data[0]["id"] if res.data else None


def _audit_finish(audit_id: str, connector_type: str, status: str, counts: dict, error: str | None = None):
    _db().table("audit_log").update({
        "status": status,
        "output_data": counts,
        "metadata": {
            "connector_type": connector_type,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        },
    }).eq("id", audit_id).execute()


def _get_existing(table: str, filters: dict):
    q = _db().table(table).select("*")
    for k, v in filters.items():
        q = q.eq(k, v)
    res = q.limit(1).execute()
    return res.data[0] if res.data else None


def _upsert_company(org_id: str, connector_type: str, c: dict) -> str:
    existing = _get_existing("crm_companies", {
        "org_id": org_id,
        "external_id": c["external_id"],
        "source": connector_type,
    })
    record = {
        "org_id": org_id,
        "name": c["name"],
        "website": c.get("website") or None,
        "phone": c.get("phone") or None,
        "email": c.get("email") or None,
        "address": c.get("address") or None,
        "city": c.get("city") or None,
        "country": c.get("country") or "MX",
        "industry": c.get("industry") or None,
        "tax_id": c.get("tax_id") or None,
        "external_id": c["external_id"],
        "source": connector_type,
        "status": "active",
    }
    if existing:
        _db().table("crm_companies").update(record).eq("id", existing["id"]).execute()
        return existing["id"]
    res = _db().table("crm_companies").insert(record).execute()
    return res.data[0]["id"]


def _upsert_contact(org_id: str, connector_type: str, c: dict, company_map: dict) -> str:
    existing = _get_existing("crm_contacts", {
        "org_id": org_id,
        "external_id": c["external_id"],
        "source": connector_type,
    })
    company_id = company_map.get(c.get("company_external_id")) if c.get("company_external_id") else None
    record = {
        "org_id": org_id,
        "first_name": c.get("first_name") or c.get("name") or "Sin nombre",
        "last_name": c.get("last_name") or None,
        "email": c.get("email") or None,
        "phone": c.get("phone") or None,
        "job_title": c.get("job_title") or None,
        "company_id": company_id,
        "external_id": c["external_id"],
        "source": connector_type,
        "status": "active",
    }
    if existing:
        _db().table("crm_contacts").update(record).eq("id", existing["id"]).execute()
        return existing["id"]
    res = _db().table("crm_contacts").insert(record).execute()
    return res.data[0]["id"]


def _ensure_pipeline(org_id: str, connector_type: str) -> str:
    name = f"{connector_type.title()} Sync"
    existing = _get_existing("crm_pipelines", {"org_id": org_id, "name": name})
    if existing:
        return existing["id"]
    res = _db().table("crm_pipelines").insert({
        "org_id": org_id,
        "name": name,
        "is_default": False,
    }).execute()
    return res.data[0]["id"]


def _ensure_stage(pipeline_id: str, org_id: str, stage_name: str) -> str:
    existing = _get_existing("crm_stages", {"pipeline_id": pipeline_id, "name": stage_name})
    if existing:
        return existing["id"]
    # Calcular orden al final
    stages = _db().table("crm_stages").select("id").eq("pipeline_id", pipeline_id).execute().data or []
    res = _db().table("crm_stages").insert({
        "pipeline_id": pipeline_id,
        "org_id": org_id,
        "name": stage_name,
        "order_index": len(stages),
    }).execute()
    return res.data[0]["id"]


def _map_status(status: str) -> str:
    s = (status or "").lower()
    if s in ("won", "closedwon", "closed_won"):
        return "won"
    if s in ("lost", "closedlost", "closed_lost"):
        return "lost"
    if s in ("open", "new", "in_progress", "qualified"):
        return "open"
    return "open"


def _upsert_deal(org_id: str, connector_type: str, d: dict, pipeline_id: str,
                 company_map: dict, contact_map: dict) -> str:
    existing = _db().table("crm_deals").select("*") \
        .eq("org_id", org_id) \
        .eq("source", connector_type) \
        .eq("metadata->>external_id", d["external_id"]) \
        .limit(1).execute().data
    existing = existing[0] if existing else None

    stage_id = _ensure_stage(pipeline_id, org_id, d.get("stage_name") or "N/A")
    company_id = company_map.get(d.get("company_external_id")) if d.get("company_external_id") else None
    contact_id = contact_map.get(d.get("contact_external_id")) if d.get("contact_external_id") else None

    record = {
        "org_id": org_id,
        "pipeline_id": pipeline_id,
        "stage_id": stage_id,
        "company_id": company_id,
        "contact_id": contact_id,
        "name": d["name"],
        "value": d.get("value") or 0,
        "currency": d.get("currency") or "MXN",
        "expected_close_date": d.get("expected_close_date") or None,
        "status": _map_status(d.get("status")),
        "source": connector_type,
        "metadata": {
            "external_id": d["external_id"],
            "connector_type": connector_type,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if existing:
        _db().table("crm_deals").update(record).eq("id", existing["id"]).execute()
        return existing["id"]
    res = _db().table("crm_deals").insert(record).execute()
    return res.data[0]["id"]


def _upsert_product(org_id: str, connector_type: str, p: dict) -> str:
    existing = _db().table("erp_products").select("*") \
        .eq("org_id", org_id) \
        .eq("metadata->>external_id", p["external_id"]) \
        .limit(1).execute().data
    existing = existing[0] if existing else None

    record = {
        "org_id": org_id,
        "name": p["name"],
        "description": p.get("description") or None,
        "sku": p.get("sku") or p.get("external_id"),
        "price": p.get("price") or 0,
        "currency": p.get("currency") or "MXN",
        "stock": p.get("stock") or 0,
        "is_active": True,
        "metadata": {
            "external_id": p["external_id"],
            "connector_type": connector_type,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if existing:
        _db().table("erp_products").update(record).eq("id", existing["id"]).execute()
        product_id = existing["id"]
    else:
        res = _db().table("erp_products").insert(record).execute()
        product_id = res.data[0]["id"]

    # Asegurar un ítem de inventario vinculado
    inv_existing = _db().table("inventory_items").select("*") \
        .eq("org_id", org_id) \
        .eq("metadata->>external_id", p["external_id"]) \
        .limit(1).execute().data
    inv_existing = inv_existing[0] if inv_existing else None
    inv_record = {
        "org_id": org_id,
        "product_id": product_id,
        "name": p["name"],
        "sku": p.get("sku") or p.get("external_id"),
        "description": p.get("description") or None,
        "current_stock": p.get("stock") or 0,
        "cost_price": p.get("price") or 0,
        "sale_price": p.get("price") or 0,
        "metadata": {
            "external_id": p["external_id"],
            "connector_type": connector_type,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if inv_existing:
        _db().table("inventory_items").update(inv_record).eq("id", inv_existing["id"]).execute()
    else:
        _db().table("inventory_items").insert(inv_record).execute()

    return product_id


def _upsert_invoice(org_id: str, connector_type: str, inv: dict,
                    company_map: dict, contact_map: dict, product_map: dict):
    existing = _db().table("erp_invoices").select("*") \
        .eq("org_id", org_id) \
        .eq("metadata->>external_id", inv["external_id"]) \
        .limit(1).execute().data
    existing = existing[0] if existing else None

    # Resolver partner (empresa o contacto)
    partner_id = None
    is_company = inv.get("company_external_id") in company_map
    if is_company:
        partner_id = company_map.get(inv.get("company_external_id"))
    elif inv.get("contact_external_id") in contact_map:
        partner_id = contact_map.get(inv.get("contact_external_id"))

    record = {
        "org_id": org_id,
        "contact_id": partner_id if not is_company else None,
        "company_id": partner_id if is_company else None,
        "invoice_number": inv.get("invoice_number") or inv["external_id"],
        "status": inv.get("status") or "draft",
        "issue_date": inv.get("issue_date") or None,
        "due_date": inv.get("due_date") or None,
        "total": inv.get("total") or 0,
        "currency": inv.get("currency") or "MXN",
        "metadata": {
            "external_id": inv["external_id"],
            "connector_type": connector_type,
            "synced_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    if existing:
        _db().table("erp_invoices").update(record).eq("id", existing["id"]).execute()
        invoice_id = existing["id"]
    else:
        res = _db().table("erp_invoices").insert(record).execute()
        invoice_id = res.data[0]["id"]

    # Reemplazar líneas de factura
    _db().table("erp_invoice_items").delete().eq("invoice_id", invoice_id).execute()
    for item in inv.get("items") or []:
        line = {
            "org_id": org_id,
            "invoice_id": invoice_id,
            "product_id": product_map.get(item.get("external_product_id")),
            "description": item.get("description") or "Concepto",
            "quantity": item.get("quantity") or 1,
            "unit_price": item.get("unit_price") or 0,
            "amount": (item.get("quantity") or 1) * (item.get("unit_price") or 0),
        }
        _db().table("erp_invoice_items").insert(line).execute()
    return invoice_id


def _persist(org_id: str, connector_type: str, data: dict) -> dict:
    counts = {"companies": 0, "contacts": 0, "deals": 0, "products": 0, "invoices": 0}
    company_map: dict[str, str] = {}
    contact_map: dict[str, str] = {}
    product_map: dict[str, str] = {}

    for c in data.get("companies") or []:
        company_map[c["external_id"]] = _upsert_company(org_id, connector_type, c)
        counts["companies"] += 1

    for c in data.get("contacts") or []:
        contact_map[c["external_id"]] = _upsert_contact(org_id, connector_type, c, company_map)
        counts["contacts"] += 1

    if data.get("deals"):
        pipeline_id = _ensure_pipeline(org_id, connector_type)
        for d in data["deals"]:
            _upsert_deal(org_id, connector_type, d, pipeline_id, company_map, contact_map)
            counts["deals"] += 1

    for p in data.get("products") or []:
        product_map[p["external_id"]] = _upsert_product(org_id, connector_type, p)
        counts["products"] += 1

    for inv in data.get("invoices") or []:
        _upsert_invoice(org_id, connector_type, inv, company_map, contact_map, product_map)
        counts["invoices"] += 1

    return counts


def _update_rag_summary(org_id: str, connector_type: str, counts: dict):
    try:
        from core.rag.context import add_source
        summary = (
            f"Sincronización {connector_type}: "
            f"{counts.get('companies', 0)} empresas, "
            f"{counts.get('contacts', 0)} contactos, "
            f"{counts.get('deals', 0)} deals, "
            f"{counts.get('products', 0)} productos, "
            f"{counts.get('invoices', 0)} facturas."
        )
        add_source(
            org_id=org_id,
            content=summary,
            source_type="connector_sync",
            name=f"Resumen {connector_type} sync",
            always_include=True,
            metadata={"connector_type": connector_type, "counts": counts},
        )
    except Exception as e:
        log.warning(f"[sync] RAG summary failed: {e}")


def run_sync(org_id: str, connector_type: str, credentials: dict) -> dict:
    provider = PROVIDERS.get(connector_type)
    if not provider:
        raise ValueError(f"Proveedor de sync no soportado: {connector_type}")

    audit_id = _audit_start(org_id, connector_type)
    log.info(f"[sync] Starting {connector_type} sync for org {org_id}")
    try:
        data = provider(credentials)
        counts = _persist(org_id, connector_type, data)
        _audit_finish(audit_id, connector_type, "ok", counts)
        _update_rag_summary(org_id, connector_type, counts)
        log.info(f"[sync] {connector_type} sync completed: {counts}")
        return {
            "status": "completed",
            "connector_type": connector_type,
            "counts": counts,
        }
    except Exception as e:
        log.error(f"[sync] {connector_type} sync failed: {e}")
        _audit_finish(audit_id, connector_type, "error", {}, error=str(e))
        raise
