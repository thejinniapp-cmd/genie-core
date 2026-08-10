"""api/routes/inventory.py — Inventario + Compras."""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import os
from datetime import datetime, timezone, date
from supabase import create_client

from api.auth import get_current_org
from core.workflows.scheduler import schedule_module_tick

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _ensure_inventory_enabled(org_id: str):
    row = (
        _db()
        .table("organization_modules")
        .select("enabled")
        .eq("organization_id", org_id)
        .eq("module_key", "inventory")
        .maybe_single()
        .execute()
        .data
    )
    if not row or not row.get("enabled"):
        raise HTTPException(403, "El módulo Inventario + Compras no está activado")


# ── Artículos / Productos de inventario ─────────────────────────────────────────

class ItemCreate(BaseModel):
    name: str
    sku: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = "pza"
    current_stock: Optional[float] = 0
    min_stock: Optional[float] = 0
    reorder_point: Optional[float] = 0
    cost_price: Optional[float] = 0
    sale_price: Optional[float] = 0
    location: Optional[str] = None
    product_id: Optional[str] = None


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    sku: Optional[str] = None
    description: Optional[str] = None
    unit: Optional[str] = None
    current_stock: Optional[float] = None
    min_stock: Optional[float] = None
    reorder_point: Optional[float] = None
    cost_price: Optional[float] = None
    sale_price: Optional[float] = None
    location: Optional[str] = None
    is_active: Optional[bool] = None
    product_id: Optional[str] = None


@router.get("/items")
def list_items(org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    rows = _db().table("inventory_items").select("*, erp_products(name)").eq("org_id", org_id).eq("is_active", True).order("name").execute().data or []
    return rows


@router.post("/items")
def create_item(body: ItemCreate, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    try:
        res = _db().table("inventory_items").insert(data).execute()
    except Exception as e:
        raise HTTPException(409, f"SKU duplicado o error de integridad: {e}")
    schedule_module_tick(org_id, "inventory", background_tasks)
    return res.data[0]


@router.patch("/items/{item_id}")
def update_item(item_id: str, body: ItemUpdate, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("inventory_items").update(data).eq("id", item_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Artículo no encontrado")
    schedule_module_tick(org_id, "inventory", background_tasks)
    return res.data[0]


@router.delete("/items/{item_id}")
def delete_item(item_id: str, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    _db().table("inventory_items").update({"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", item_id).eq("org_id", org_id).execute()
    return {"status": "deleted"}


# ── Proveedores ────────────────────────────────────────────────────────────────

class SupplierCreate(BaseModel):
    name: str
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    tax_id: Optional[str] = None
    address: Optional[str] = None


class SupplierUpdate(BaseModel):
    name: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    tax_id: Optional[str] = None
    address: Optional[str] = None
    is_active: Optional[bool] = None


@router.get("/suppliers")
def list_suppliers(org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    rows = _db().table("inventory_suppliers").select("*").eq("org_id", org_id).eq("is_active", True).order("name").execute().data or []
    return rows


@router.post("/suppliers")
def create_supplier(body: SupplierCreate, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    res = _db().table("inventory_suppliers").insert(data).execute()
    return res.data[0]


@router.patch("/suppliers/{supplier_id}")
def update_supplier(supplier_id: str, body: SupplierUpdate, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("inventory_suppliers").update(data).eq("id", supplier_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Proveedor no encontrado")
    return res.data[0]


@router.delete("/suppliers/{supplier_id}")
def delete_supplier(supplier_id: str, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    _db().table("inventory_suppliers").update({"is_active": False, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", supplier_id).eq("org_id", org_id).execute()
    return {"status": "deleted"}


# ── Órdenes de compra ──────────────────────────────────────────────────────────

class PurchaseOrderItem(BaseModel):
    item_id: str
    quantity: float = 1
    unit_cost: float = 0


class PurchaseOrderCreate(BaseModel):
    supplier_id: Optional[str] = None
    order_date: Optional[str] = None
    expected_date: Optional[str] = None
    notes: Optional[str] = None
    items: List[PurchaseOrderItem]


class PurchaseOrderUpdate(BaseModel):
    supplier_id: Optional[str] = None
    order_date: Optional[str] = None
    expected_date: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


@router.get("/purchase-orders")
def list_purchase_orders(org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    rows = (
        _db()
        .table("inventory_purchase_orders")
        .select("*, inventory_suppliers(name), inventory_purchase_order_items(*, inventory_items(name))")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    return rows


@router.post("/purchase-orders")
def create_purchase_order(body: PurchaseOrderCreate, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    total = sum(round(i.quantity * i.unit_cost, 2) for i in body.items)
    po_data = {
        "org_id": org_id,
        "supplier_id": body.supplier_id,
        "order_date": body.order_date or date.today().isoformat(),
        "expected_date": body.expected_date,
        "total": total,
        "notes": body.notes,
        "status": "draft",
    }
    res = _db().table("inventory_purchase_orders").insert(po_data).execute()
    po = res.data[0]

    items_data = []
    for i in body.items:
        items_data.append({
            "org_id": org_id,
            "order_id": po["id"],
            "item_id": i.item_id,
            "quantity": i.quantity,
            "unit_cost": i.unit_cost,
            "total": round(i.quantity * i.unit_cost, 2),
        })
    _db().table("inventory_purchase_order_items").insert(items_data).execute()
    return po


@router.post("/purchase-orders/{po_id}/receive")
def receive_purchase_order(po_id: str, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    db = _db()
    po = db.table("inventory_purchase_orders").select("*").eq("id", po_id).eq("org_id", org_id).single().execute().data
    if not po:
        raise HTTPException(404, "Orden de compra no encontrada")
    if po.get("status") in ("received", "cancelled"):
        raise HTTPException(400, "La orden ya fue recibida o cancelada")

    items = db.table("inventory_purchase_order_items").select("*").eq("order_id", po_id).eq("org_id", org_id).execute().data or []
    for line in items:
        qty = float(line.get("quantity") or 0) - float(line.get("received_qty") or 0)
        if qty <= 0:
            continue
        item_id = line.get("item_id")
        # Actualizar stock del artículo
        item = db.table("inventory_items").select("current_stock").eq("id", item_id).eq("org_id", org_id).single().execute().data
        if item:
            new_stock = float(item.get("current_stock") or 0) + qty
            db.table("inventory_items").update({"current_stock": new_stock, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", item_id).execute()
        # Registrar movimiento de entrada
        db.table("inventory_stock_movements").insert({
            "org_id": org_id,
            "item_id": item_id,
            "type": "in",
            "quantity": qty,
            "reference": f"PO {po_id}",
            "related_po_id": po_id,
            "reason": "Recepción de orden de compra",
        }).execute()
        # Marcar línea como recibida
        db.table("inventory_purchase_order_items").update({"received_qty": line.get("quantity"), "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", line["id"]).execute()

    db.table("inventory_purchase_orders").update({"status": "received", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", po_id).execute()
    schedule_module_tick(org_id, "inventory", background_tasks)
    return {"status": "received"}


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_purchase_order(po_id: str, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    db = _db()
    po = db.table("inventory_purchase_orders").select("*").eq("id", po_id).eq("org_id", org_id).single().execute().data
    if not po:
        raise HTTPException(404, "Orden de compra no encontrada")
    db.table("inventory_purchase_orders").update({"status": "cancelled", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", po_id).execute()
    return {"status": "cancelled"}


@router.patch("/purchase-orders/{po_id}")
def update_purchase_order(po_id: str, body: PurchaseOrderUpdate, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("inventory_purchase_orders").update(data).eq("id", po_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Orden de compra no encontrada")
    return res.data[0]


# ── Movimientos de stock ───────────────────────────────────────────────────────

class StockMovementCreate(BaseModel):
    item_id: str
    type: str  # in | out | adjustment | return
    quantity: float
    reason: Optional[str] = None
    reference: Optional[str] = None


@router.get("/stock-movements")
def list_stock_movements(item_id: Optional[str] = None, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    q = (
        _db()
        .table("inventory_stock_movements")
        .select("*, inventory_items(name, sku)")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
    )
    if item_id:
        q = q.eq("item_id", item_id)
    rows = q.execute().data or []
    return rows


@router.post("/stock-movements")
def create_stock_movement(body: StockMovementCreate, background_tasks: BackgroundTasks, org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    db = _db()
    item = db.table("inventory_items").select("current_stock").eq("id", body.item_id).eq("org_id", org_id).single().execute().data
    if not item:
        raise HTTPException(404, "Artículo no encontrado")

    current = float(item.get("current_stock") or 0)
    qty = float(body.quantity)
    delta = qty if body.type in ("in", "return") else -qty
    new_stock = max(0, current + delta)

    db.table("inventory_items").update({"current_stock": new_stock, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", body.item_id).execute()
    res = db.table("inventory_stock_movements").insert({
        "org_id": org_id,
        "item_id": body.item_id,
        "type": body.type,
        "quantity": qty,
        "reason": body.reason,
        "reference": body.reference,
    }).execute()
    schedule_module_tick(org_id, "inventory", background_tasks)
    return res.data[0]


# ── Resumen de inventario ──────────────────────────────────────────────────────

@router.get("/summary")
def get_summary(org_id: str = Depends(get_current_org)):
    _ensure_inventory_enabled(org_id)
    db = _db()
    items = db.table("inventory_items").select("current_stock, min_stock, cost_price").eq("org_id", org_id).eq("is_active", True).execute().data or []
    total_items = len(items)
    low_stock = sum(1 for i in items if float(i.get("current_stock") or 0) <= float(i.get("min_stock") or 0))
    total_value = sum(float(i.get("current_stock") or 0) * float(i.get("cost_price") or 0) for i in items)
    pending_pos = db.table("inventory_purchase_orders").select("id", count="exact").eq("org_id", org_id).in_("status", ["draft", "sent", "partial"]).execute().count

    return {
        "total_items": total_items,
        "low_stock": low_stock,
        "total_value": round(total_value, 2),
        "pending_purchase_orders": pending_pos or 0,
    }
