"""api/routes/control.py — Genie Control: presupuesto vivo, KPIs, RRHH y dependencia del founder"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import os
from datetime import datetime, timezone, date, timedelta
from supabase import create_client

from api.auth import get_current_org

router = APIRouter()


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _ensure_control_enabled(org_id: str):
    row = (
        _db()
        .table("organization_modules")
        .select("enabled")
        .eq("organization_id", org_id)
        .eq("module_key", "control")
        .maybe_single()
        .execute()
        .data
    )
    if not row or not row.get("enabled"):
        raise HTTPException(403, "El módulo Genie Control no está activado")


# ── Presupuesto ───────────────────────────────────────────────────────────────

class BudgetCreate(BaseModel):
    account_id: str
    year: int
    month: int
    planned_amount: float = 0
    notes: Optional[str] = None


class BudgetUpdate(BaseModel):
    planned_amount: Optional[float] = None
    notes: Optional[str] = None


@router.get("/budgets")
def list_budgets(year: Optional[int] = None, month: Optional[int] = None, org_id: str = Depends(get_current_org)):
    _ensure_control_enabled(org_id)
    q = _db().table("erp_budgets").select("*, erp_accounts(name, type, color)").eq("org_id", org_id)
    if year:
        q = q.eq("year", year)
    if month:
        q = q.eq("month", month)
    return q.order("year", desc=True).order("month", desc=True).execute().data or []


@router.post("/budgets")
def create_budget(body: BudgetCreate, org_id: str = Depends(get_current_org)):
    _ensure_control_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    try:
        res = _db().table("erp_budgets").upsert(data, on_conflict="org_id,account_id,year,month").execute()
    except Exception as e:
        raise HTTPException(409, f"No se pudo guardar el presupuesto: {e}")
    return res.data[0]


@router.patch("/budgets/{budget_id}")
def update_budget(budget_id: str, body: BudgetUpdate, org_id: str = Depends(get_current_org)):
    _ensure_control_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("erp_budgets").update(data).eq("id", budget_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Presupuesto no encontrado")
    return res.data[0]


@router.get("/budgets/variance")
def get_budget_variance(year: Optional[int] = None, month: Optional[int] = None, org_id: str = Depends(get_current_org)):
    """Compara presupuesto vs. real por cuenta para el mes indicado (por defecto, el actual)."""
    _ensure_control_enabled(org_id)
    today = date.today()
    year = year or today.year
    month = month or today.month

    budgets = (
        _db()
        .table("erp_budgets")
        .select("account_id, planned_amount, erp_accounts(name, type, color)")
        .eq("org_id", org_id)
        .eq("year", year)
        .eq("month", month)
        .execute()
        .data
        or []
    )

    month_start = date(year, month, 1).isoformat()
    if month == 12:
        month_end = date(year + 1, 1, 1).isoformat()
    else:
        month_end = date(year, month + 1, 1).isoformat()
    transactions = (
        _db()
        .table("erp_transactions")
        .select("account_id, amount")
        .eq("org_id", org_id)
        .gte("transaction_date", month_start)
        .lt("transaction_date", month_end)
        .execute()
        .data
        or []
    )
    actual_by_account = {}
    for t in transactions:
        aid = t.get("account_id")
        if not aid:
            continue
        actual_by_account[aid] = actual_by_account.get(aid, 0.0) + float(t.get("amount") or 0)

    rows = []
    for b in budgets:
        planned = float(b.get("planned_amount") or 0)
        actual = actual_by_account.get(b["account_id"], 0.0)
        account = b.get("erp_accounts") or {}
        variance_pct = round(((actual - planned) / planned) * 100, 1) if planned > 0 else None
        narrative = None
        if variance_pct is not None:
            direction = "más" if variance_pct > 0 else "menos"
            narrative = f"{account.get('name', 'Cuenta')} gastó {abs(variance_pct)}% {direction} de lo presupuestado."
        rows.append({
            "account_id": b["account_id"],
            "account_name": account.get("name"),
            "account_type": account.get("type"),
            "planned": planned,
            "actual": actual,
            "variance_pct": variance_pct,
            "narrative": narrative,
        })

    return {"year": year, "month": month, "accounts": rows}


# ── KPIs ──────────────────────────────────────────────────────────────────────

@router.get("/kpis")
def get_kpis(days: int = 90, org_id: str = Depends(get_current_org)):
    """Rentabilidad por producto, recurrencia de clientes y dependencia del founder."""
    _ensure_control_enabled(org_id)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

    # Rentabilidad por producto (revenue de erp_invoice_items, margen si hay cost_price)
    invoice_ids_in_window = [
        r["id"]
        for r in _db().table("erp_invoices").select("id").eq("org_id", org_id).gte("issue_date", since).execute().data or []
    ]
    items = []
    if invoice_ids_in_window:
        items = (
            _db()
            .table("erp_invoice_items")
            .select("product_id, quantity, amount, erp_products(name, cost_price)")
            .eq("org_id", org_id)
            .in_("invoice_id", invoice_ids_in_window)
            .execute()
            .data
            or []
        )
    by_product: dict = {}
    for it in items:
        pid = it.get("product_id") or "sin_producto"
        product = it.get("erp_products") or {}
        entry = by_product.setdefault(pid, {
            "product_id": pid if pid != "sin_producto" else None,
            "product_name": product.get("name") or "Sin producto",
            "revenue": 0.0,
            "cost": 0.0,
            "has_cost_data": bool(product.get("cost_price")),
            "quantity": 0.0,
        })
        entry["revenue"] += float(it.get("amount") or 0)
        entry["quantity"] += float(it.get("quantity") or 0)
        entry["cost"] += float(it.get("quantity") or 0) * float(product.get("cost_price") or 0)

    profitability = []
    for entry in by_product.values():
        margin = None
        margin_pct = None
        if entry["has_cost_data"]:
            margin = round(entry["revenue"] - entry["cost"], 2)
            margin_pct = round((margin / entry["revenue"]) * 100, 1) if entry["revenue"] > 0 else None
        profitability.append({
            "product_id": entry["product_id"],
            "product_name": entry["product_name"],
            "revenue": round(entry["revenue"], 2),
            "quantity": entry["quantity"],
            "margin": margin,
            "margin_pct": margin_pct,
        })
    profitability.sort(key=lambda r: r["revenue"], reverse=True)

    # Recurrencia de clientes
    invoices = (
        _db()
        .table("erp_invoices")
        .select("contact_id, issue_date")
        .eq("org_id", org_id)
        .gte("issue_date", since)
        .execute()
        .data
        or []
    )
    invoices_per_contact: dict = {}
    for inv in invoices:
        cid = inv.get("contact_id")
        if not cid:
            continue
        invoices_per_contact[cid] = invoices_per_contact.get(cid, 0) + 1
    total_contacts = len(invoices_per_contact)
    recurring_contacts = sum(1 for c in invoices_per_contact.values() if c >= 2)
    recurrence_pct = round((recurring_contacts / total_contacts) * 100, 1) if total_contacts > 0 else None

    return {
        "window_days": days,
        "profitability_by_product": profitability,
        "customer_recurrence": {
            "total_customers": total_contacts,
            "recurring_customers": recurring_contacts,
            "recurrence_pct": recurrence_pct,
        },
        "founder_dependency": _founder_dependency_summary(org_id),
    }


# ── RRHH ──────────────────────────────────────────────────────────────────────

class EmployeeCreate(BaseModel):
    name: str
    email: Optional[str] = None
    role_title: Optional[str] = None
    department: Optional[str] = None
    is_founder: bool = False
    is_manager: bool = False
    manager_id: Optional[str] = None
    hire_date: Optional[str] = None


class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    role_title: Optional[str] = None
    department: Optional[str] = None
    is_founder: Optional[bool] = None
    is_manager: Optional[bool] = None
    manager_id: Optional[str] = None
    status: Optional[str] = None
    hire_date: Optional[str] = None
    termination_date: Optional[str] = None


@router.get("/employees")
def list_employees(status: Optional[str] = "active", org_id: str = Depends(get_current_org)):
    _ensure_control_enabled(org_id)
    q = _db().table("hr_employees").select("*").eq("org_id", org_id)
    if status:
        q = q.eq("status", status)
    return q.order("name").execute().data or []


@router.post("/employees")
def create_employee(body: EmployeeCreate, org_id: str = Depends(get_current_org)):
    _ensure_control_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["org_id"] = org_id
    try:
        res = _db().table("hr_employees").insert(data).execute()
    except Exception as e:
        raise HTTPException(409, f"No se pudo crear el empleado: {e}")
    return res.data[0]


@router.patch("/employees/{employee_id}")
def update_employee(employee_id: str, body: EmployeeUpdate, org_id: str = Depends(get_current_org)):
    _ensure_control_enabled(org_id)
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = _db().table("hr_employees").update(data).eq("id", employee_id).eq("org_id", org_id).execute()
    if not res.data:
        raise HTTPException(404, "Empleado no encontrado")
    return res.data[0]


@router.get("/employees/workload")
def get_employee_workload(org_id: str = Depends(get_current_org)):
    """Carga por empleado: tareas de workflow abiertas asignadas a su email."""
    _ensure_control_enabled(org_id)
    employees = (
        _db()
        .table("hr_employees")
        .select("id, name, email")
        .eq("org_id", org_id)
        .eq("status", "active")
        .execute()
        .data
        or []
    )
    run_ids = [r["id"] for r in _db().table("workflow_runs").select("id").eq("org_id", org_id).execute().data or []]
    step_ids = []
    if run_ids:
        step_ids = [
            r["id"]
            for r in _db().table("workflow_run_steps").select("id").in_("run_id", run_ids).execute().data or []
        ]
    open_tasks = []
    if step_ids:
        open_tasks = (
            _db()
            .table("workflow_run_tasks")
            .select("assigned_to, status")
            .in_("run_step_id", step_ids)
            .in_("status", ["pending", "in_progress"])
            .execute()
            .data
            or []
        )
    counts: dict = {}
    for t in open_tasks:
        email = t.get("assigned_to")
        if not email:
            continue
        counts[email] = counts.get(email, 0) + 1

    return [
        {"employee_id": e["id"], "name": e["name"], "email": e.get("email"), "open_tasks": counts.get(e.get("email"), 0)}
        for e in employees
    ]


@router.get("/employees/turnover")
def get_employee_turnover(days: int = 180, org_id: str = Depends(get_current_org)):
    """Rotación por departamento: empleados inactivos con baja en la ventana."""
    _ensure_control_enabled(org_id)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

    all_employees = _db().table("hr_employees").select("department, status, termination_date").eq("org_id", org_id).execute().data or []
    by_department: dict = {}
    for e in all_employees:
        dept = e.get("department") or "Sin área"
        entry = by_department.setdefault(dept, {"department": dept, "active": 0, "left_in_window": 0})
        if e.get("status") == "active":
            entry["active"] += 1
        elif e.get("status") == "inactive" and e.get("termination_date") and e["termination_date"] >= since:
            entry["left_in_window"] += 1

    rows = []
    for entry in by_department.values():
        base = entry["active"] + entry["left_in_window"]
        entry["turnover_pct"] = round((entry["left_in_window"] / base) * 100, 1) if base > 0 else 0.0
        rows.append(entry)
    return {"window_days": days, "by_department": rows}


# ── Dependencia del founder ─────────────────────────────────────────────────────

def _founder_dependency_summary(org_id: str, days: int = 30) -> dict:
    founder = (
        _db()
        .table("hr_employees")
        .select("id, name, email")
        .eq("org_id", org_id)
        .eq("is_founder", True)
        .eq("status", "active")
        .limit(1)
        .execute()
        .data
        or []
    )
    founder = founder[0] if founder else None
    if not founder or not founder.get("email"):
        return {"founder": None, "dependency_pct": None, "message": "No hay founder registrado con email"}

    since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    run_ids = [
        r["id"]
        for r in _db().table("workflow_runs").select("id").eq("org_id", org_id).gte("started_at", since_iso).execute().data or []
    ]
    step_ids = []
    if run_ids:
        step_ids = [
            r["id"]
            for r in _db().table("workflow_run_steps").select("id").in_("run_id", run_ids).execute().data or []
        ]
    tasks = []
    if step_ids:
        tasks = (
            _db()
            .table("workflow_run_tasks")
            .select("assigned_to, type, status")
            .in_("run_step_id", step_ids)
            .in_("status", ["approved", "completed"])
            .eq("type", "human_internal")
            .execute()
            .data
            or []
        )
    if not tasks:
        return {"founder": founder, "dependency_pct": None, "total_human_tasks": 0}

    founder_tasks = sum(1 for t in tasks if t.get("assigned_to") == founder["email"])
    return {
        "founder": founder,
        "dependency_pct": round((founder_tasks / len(tasks)) * 100, 1),
        "founder_tasks": founder_tasks,
        "total_human_tasks": len(tasks),
    }


@router.get("/founder-dependency")
def get_founder_dependency(org_id: str = Depends(get_current_org)):
    """Detalle de pasos de workflow que resuelve repetidamente el founder — candidatos a delegar."""
    _ensure_control_enabled(org_id)

    summary = _founder_dependency_summary(org_id)
    founder = summary.get("founder")
    if not founder:
        return {**summary, "bottlenecks": []}

    since_iso = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    template_ids = [r["id"] for r in _db().table("workflow_templates").select("id").eq("org_id", org_id).execute().data or []]
    steps = []
    if template_ids:
        steps = (
            _db()
            .table("workflow_steps")
            .select("id, name, template_id, assigned_to")
            .in_("template_id", template_ids)
            .eq("assigned_to", founder["email"])
            .execute()
            .data
            or []
        )
    bottlenecks = []
    if steps:
        step_ids = [s["id"] for s in steps]
        run_steps = (
            _db()
            .table("workflow_run_steps")
            .select("step_id, status, completed_at")
            .in_("step_id", step_ids)
            .eq("status", "completed")
            .gte("completed_at", since_iso)
            .execute()
            .data
            or []
        )
        counts: dict = {}
        for rs in run_steps:
            counts[rs["step_id"]] = counts.get(rs["step_id"], 0) + 1
        for step in steps:
            occurrences = counts.get(step["id"], 0)
            if occurrences == 0:
                continue
            bottlenecks.append({
                "step_id": step["id"],
                "step_name": step["name"],
                "template_id": step["template_id"],
                "occurrences_last_90d": occurrences,
            })
        bottlenecks.sort(key=lambda b: b["occurrences_last_90d"], reverse=True)

    return {**summary, "bottlenecks": bottlenecks}
