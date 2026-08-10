"""api/routes/collections.py — Cobranza + Facturación"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import os
from datetime import datetime, timezone, date
from supabase import create_client

from api.auth import get_current_org
from core.workflows.scheduler import start_event_run, schedule_module_tick

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _ensure_collections_enabled(org_id: str):
    row = (
        _db()
        .table("organization_modules")
        .select("enabled")
        .eq("organization_id", org_id)
        .eq("module_key", "collections")
        .maybe_single()
        .execute()
    )
    data = row.data if row else None
    if not data or not data.get("enabled"):
        raise HTTPException(403, "El módulo Cobranza + Facturación no está activado")


# ── Productos ───────────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = 0
    currency: Optional[str] = "MXN"
    stock: Optional[float] = 0


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None
    stock: Optional[float] = None
    is_active: Optional[bool] = None


@router.get("/products")
def list_products(org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    rows = _db().table("erp_products").select("*").eq("org_id", org_id).eq("is_active", True).order("name").execute().data or []
    return rows


@router.post("/products")
def create_product(body: ProductCreate, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    res = _db().table("erp_products").insert(data).execute()
    return res.data[0]


@router.patch("/products/{product_id}")
def update_product(product_id: str, body: ProductUpdate, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("erp_products").update(data).eq("id", product_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Producto no encontrado")
    return res.data[0]


# ── Facturas ──────────────────────────────────────────────────────────────────

class InvoiceItem(BaseModel):
    description: str
    quantity: float = 1
    unit_price: float = 0
    product_id: Optional[str] = None


class InvoiceCreate(BaseModel):
    contact_id: Optional[str] = None
    company_id: Optional[str] = None
    invoice_number: str
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    tax_rate: Optional[float] = 0.16
    discount: Optional[float] = 0
    notes: Optional[str] = None
    items: List[InvoiceItem]


class InvoiceUpdate(BaseModel):
    contact_id: Optional[str] = None
    company_id: Optional[str] = None
    invoice_number: Optional[str] = None
    issue_date: Optional[str] = None
    due_date: Optional[str] = None
    tax_rate: Optional[float] = None
    discount: Optional[float] = None
    notes: Optional[str] = None
    status: Optional[str] = None


@router.get("/invoices")
def list_invoices(status: Optional[str] = None, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    q = _db().table("erp_invoices").select("*, crm_contacts(first_name,last_name), crm_companies(name), erp_invoice_items(*), erp_payments(*)").eq("org_id", org_id)
    if status:
        q = q.eq("status", status)
    rows = q.order("issue_date", desc=True).execute().data or []
    return rows


@router.post("/invoices")
def create_invoice(body: InvoiceCreate, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)

    items_data = []
    subtotal = 0
    for item in body.items:
        amount = round(item.quantity * item.unit_price, 2)
        subtotal += amount
        items_data.append({
            "org_id": org_id,
            "product_id": item.product_id,
            "description": item.description,
            "quantity": item.quantity,
            "unit_price": item.unit_price,
            "amount": amount,
        })

    tax_amount = round(subtotal * (body.tax_rate or 0.16), 2)
    total = round(subtotal + tax_amount - (body.discount or 0), 2)

    invoice_data = {
        "org_id": org_id,
        "contact_id": body.contact_id,
        "company_id": body.company_id,
        "invoice_number": body.invoice_number,
        "issue_date": body.issue_date or date.today().isoformat(),
        "due_date": body.due_date,
        "subtotal": subtotal,
        "tax_rate": body.tax_rate or 0.16,
        "tax_amount": tax_amount,
        "discount": body.discount or 0,
        "total": total,
        "status": "draft",
        "notes": body.notes,
    }

    res = _db().table("erp_invoices").insert(invoice_data).execute()
    invoice = res.data[0]

    for item in items_data:
        item["invoice_id"] = invoice["id"]
    _db().table("erp_invoice_items").insert(items_data).execute()

    return invoice


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    row = _db().table("erp_invoices").select("*, crm_contacts(first_name,last_name), crm_companies(name), erp_invoice_items(*), erp_payments(*)").eq("id", invoice_id).eq("org_id", org_id).single().execute().data
    if not row:
        raise HTTPException(404, "Factura no encontrada")
    return row


@router.patch("/invoices/{invoice_id}")
def update_invoice(invoice_id: str, body: InvoiceUpdate, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("erp_invoices").update(data).eq("id", invoice_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Factura no encontrada")
    schedule_module_tick(org_id, "collections", background_tasks)
    return res.data[0]


@router.post("/invoices/{invoice_id}/send")
def send_invoice(invoice_id: str, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    res = _db().table("erp_invoices").update({"status": "sent", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", invoice_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Factura no encontrada")
    schedule_module_tick(org_id, "collections", background_tasks)
    return res.data[0]


@router.post("/invoices/{invoice_id}/cancel")
def cancel_invoice(invoice_id: str, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)
    res = _db().table("erp_invoices").update({"status": "cancelled", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", invoice_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Factura no encontrada")
    return res.data[0]


# ── Pagos ───────────────────────────────────────────────────────────────────────

class PaymentCreate(BaseModel):
    invoice_id: str
    amount: float
    payment_date: Optional[str] = None
    method: Optional[str] = "transfer"
    reference: Optional[str] = None
    notes: Optional[str] = None


@router.post("/payments")
def create_payment(body: PaymentCreate, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)

    invoice = _db().table("erp_invoices").select("total").eq("id", body.invoice_id).eq("org_id", org_id).single().execute().data
    if not invoice:
        raise HTTPException(404, "Factura no encontrada")

    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    data["payment_date"] = body.payment_date or date.today().isoformat()
    res = _db().table("erp_payments").insert(data).execute()

    # Actualizar estado de la factura según total pagado
    payments = _db().table("erp_payments").select("amount").eq("invoice_id", body.invoice_id).execute().data or []
    total_paid = sum(float(p["amount"] or 0) for p in payments)

    new_status = "sent"
    if total_paid >= invoice["total"]:
        new_status = "paid"
    elif total_paid > 0:
        new_status = "partial"

    _db().table("erp_invoices").update({"status": new_status, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", body.invoice_id).execute()

    # Disparar flujo automático "Pago recibido"
    try:
        start_event_run(
            org_id=org_id,
            module_key="collections",
            event="payment_received",
            input_data={
                "payment_id": res.data[0]["id"],
                "invoice_id": body.invoice_id,
                "amount": float(res.data[0].get("amount") or 0),
            },
            name=f"Pago recibido: factura {body.invoice_id}",
            metadata={"invoice_id": body.invoice_id, "payment_id": res.data[0]["id"]},
        )
    except Exception as e:
        log.warning(f"[collections] Could not trigger payment_received workflow: {e}")

    # Disparar flujo contable "Ingreso automático por pago" si aplica
    try:
        start_event_run(
            org_id=org_id,
            module_key="accounting",
            event="payment_received_accounting",
            input_data={
                "payment_id": res.data[0]["id"],
                "invoice_id": body.invoice_id,
                "amount": float(res.data[0].get("amount") or 0),
            },
            name=f"Ingreso contable: factura {body.invoice_id}",
            metadata={"invoice_id": body.invoice_id, "payment_id": res.data[0]["id"]},
        )
    except Exception as e:
        log.warning(f"[collections] Could not trigger payment_received_accounting workflow: {e}")

    schedule_module_tick(org_id, "collections", background_tasks)

    return res.data[0]


# ── Resumen / Dashboard de cobranza ───────────────────────────────────────────

@router.get("/summary")
def get_summary(org_id: str = Depends(get_current_org)):
    _ensure_collections_enabled(org_id)

    today = date.today().isoformat()

    invoices = _db().table("erp_invoices").select("status,total").eq("org_id", org_id).execute().data or []

    total_outstanding = 0
    total_overdue = 0
    total_paid = 0
    for inv in invoices:
        if inv["status"] == "paid":
            total_paid += float(inv["total"] or 0)
        elif inv["status"] in ("sent", "partial"):
            total_outstanding += float(inv["total"] or 0)
            if inv.get("due_date") and inv["due_date"] < today:
                total_overdue += float(inv["total"] or 0)

    return {
        "total_invoiced": sum(float(i["total"] or 0) for i in invoices),
        "total_paid": total_paid,
        "total_outstanding": total_outstanding,
        "total_overdue": total_overdue,
        "invoice_count": len(invoices),
    }
