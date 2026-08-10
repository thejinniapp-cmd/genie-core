import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from supabase import create_client

from api.auth import get_current_org

router = APIRouter(prefix="/api/adn", tags=["adn"])


def _db():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


# ── Models ────────────────────────────────────────────────────────────────────

class StreamAdnPayload(BaseModel):
    adn_id: str
    is_lead: bool = False
    sort_order: int = 0

class HierarchyPayload(BaseModel):
    manager_adn_id: str
    report_adn_id: str

class CreateProfilePayload(BaseModel):
    name: str
    role: str
    headline: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    skills: list[str] = []
    frameworks: list[str] = []
    category: str = "operativo"  # 'directivo' | 'especialista' | 'operativo'
    is_manager: bool = False
    talent_type: str = "human"  # 'ai' | 'human'


# ── Catalog (global, no org filter — any active ADN is visible) ───────────────

@router.get("/catalog")
def list_catalog(org_id: str = Depends(get_current_org)):
    """All ADNs the org has access to (via org_adn), plus official AI staff agents."""
    db = _db()
    rows = (
        db.table("org_adn")
        .select("status, activated_at, adn_catalog(id, slug, name, role, headline, bio, avatar_url, skills, frameworks, category, is_manager, tier, sort_order, talent_type)")
        .eq("org_id", org_id)
        .eq("status", "active")
        .execute()
    )
    # Flatten nested adn_catalog join
    result = []
    for row in (rows.data or []):
        adn = row.get("adn_catalog") or {}
        adn["org_status"] = row["status"]
        adn["activated_at"] = row["activated_at"]
        result.append(adn)

    # Agregar AI Staff oficial del marketplace como perfiles de AI
    enabled_staff = {
        s["staff_key"]: s
        for s in (db.table("organization_ai_staff").select("staff_key, enabled, enabled_at").eq("organization_id", org_id).execute().data or [])
    }
    staff_items = db.table("marketplace_items").select("*").eq("item_type", "ai_staff").eq("is_public", True).execute().data or []

    STAFF_PROFILES = {
        "chief_of_staff": {
            "role": "Asistente Ejecutivo",
            "category": "directivo",
            "is_manager": True,
            "skills": [
                "Coordinación ejecutiva",
                "Gestión de agendas y tareas",
                "Recordatorios inteligentes",
                "Resúmenes ejecutivos",
                "Priorización urgente vs importante",
                "Comunicación diplomática",
            ],
            "frameworks": ["ReAct", "Chain-of-Thought", "Plan-and-Solve"],
            "bio": """Opera en todos los módulos de Genie como mano derecha del ejecutivo.

Responsabilidades:
• Coordina tareas entre los agentes especializados y el equipo humano.
• Genera resúmenes diarios/semanales de CRM, Cobranza, Contabilidad e Inventario.
• Sugiere recordatorios y próximos pasos prioritarios.
• Filtra ruido y destaca lo urgente vs importante.

Módulos: CRM, Cobranza, Contabilidad, Inventario, Streams.""",
        },
        "sales_agent": {
            "role": "Especialista en Pipeline",
            "category": "especialista",
            "is_manager": False,
            "skills": [
                "Seguimiento de oportunidades",
                "Detección de deals estancados",
                "Redacción de correos de ventas",
                "Análisis de historial de contacto",
                "CRM y pipeline",
            ],
            "frameworks": ["BANT", "MEDDIC", "Follow-up cadence", "CRM automation"],
            "bio": """Opera dentro del módulo CRM / Ventas.

Responsabilidades:
• Escanea deals abiertos sin actividad reciente.
• Sugiere y crea tareas de seguimiento personalizadas por contacto.
• Resume el historial de interacciones para preparar llamadas.
• Detecta oportunidades en riesgo de perderse.

Módulos: CRM. Requiere: módulo CRM activo.""",
        },
        "collector_agent": {
            "role": "Especialista en Cobranza",
            "category": "especialista",
            "is_manager": False,
            "skills": [
                "Detección de facturas vencidas",
                "Recordatorios de pago amables",
                "Cobranza proactiva",
                "Reconciliación de pagos",
                "Cuentas por cobrar",
            ],
            "frameworks": ["Dunning cadence", "Cuentas por cobrar", "Collections workflow"],
            "bio": """Opera dentro del módulo Cobranza + Facturación.

Responsabilidades:
• Revisa facturas vencidas o próximas a vencer.
• Genera acciones de cobranza (tareas de CRM o recordatorios internos).
• Prioriza deudores según monto y antigüedad.
• Ayuda a reducir días de ventas outstanding (DSO).

Módulos: Cobranza. Requiere: módulo Cobranza activo; CRM opcional para tareas.""",
        },
    }

    for item in staff_items:
        meta = item.get("metadata") or {}
        staff_key = item["slug"]
        enabled = enabled_staff.get(staff_key, {}).get("enabled", False)
        profile = STAFF_PROFILES.get(staff_key, {})
        result.append({
            "id": item["id"],
            "slug": staff_key,
            "name": item["name"],
            "role": profile.get("role", "Agente AI"),
            "headline": meta.get("tagline") or item["description"],
            "bio": profile.get("bio", item["description"]),
            "avatar_url": None,
            "skills": profile.get("skills", list(item.get("dependencies") or [])) or [],
            "frameworks": profile.get("frameworks", list(item.get("connectors_required") or [])) or [],
            "category": profile.get("category", "operativo"),
            "is_manager": profile.get("is_manager", False),
            "tier": "free",
            "sort_order": 999,
            "talent_type": "ai",
            "org_status": "active" if enabled else "inactive",
            "is_ai_staff": True,
            "staff_key": staff_key,
        })

    result.sort(key=lambda x: x.get("sort_order", 99))
    return result


@router.get("/catalog/all")
def list_all_catalog():
    """Full ADN catalog (super admin view — no org filter)."""
    rows = _db().table("adn_catalog").select("*").order("sort_order").execute()
    return rows.data or []


@router.post("/catalog")
def create_profile(payload: CreateProfilePayload, org_id: str = Depends(get_current_org)):
    """Crea un perfil (humano o AI) en el catálogo y lo activa para la org."""
    db = _db()

    max_sort = db.table("adn_catalog").select("sort_order").order("sort_order", desc=True).limit(1).execute()
    next_sort = (max_sort.data[0]["sort_order"] + 1) if max_sort.data else 0

    slug = payload.name.lower().replace(" ", "-")

    row = db.table("adn_catalog").insert({
        "slug": slug,
        "name": payload.name,
        "role": payload.role,
        "headline": payload.headline,
        "bio": payload.bio,
        "avatar_url": payload.avatar_url,
        "skills": payload.skills,
        "frameworks": payload.frameworks,
        "category": payload.category,
        "is_manager": payload.is_manager,
        "talent_type": payload.talent_type,
        "tier": "enterprise",
        "sort_order": next_sort,
    }).execute()

    if not row.data:
        raise HTTPException(status_code=500, detail="Error creando perfil")

    adn_id = row.data[0]["id"]

    db.table("org_adn").insert({
        "org_id": org_id,
        "adn_id": adn_id,
        "status": "active",
    }).execute()

    return row.data[0]


class UpdateProfilePayload(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    headline: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    skills: Optional[list[str]] = None
    frameworks: Optional[list[str]] = None
    category: Optional[str] = None
    is_manager: Optional[bool] = None


@router.patch("/catalog/{adn_id}")
def update_profile(adn_id: str, payload: UpdateProfilePayload, org_id: str = Depends(get_current_org)):
    """Edita un perfil existente del catálogo (org debe tenerlo activo)."""
    db = _db()

    access = db.table("org_adn").select("id").eq("org_id", org_id).eq("adn_id", adn_id).eq("status", "active").execute()
    if not access.data:
        raise HTTPException(status_code=403, detail="ADN not available for this organization")

    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="Nada que actualizar")

    row = db.table("adn_catalog").update(update).eq("id", adn_id).execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")

    return row.data[0]


@router.post("/create-secretaria")
def create_secretaria_adn(org_id: str = Depends(get_current_org)):
    """Crea el ADN de Secretaria Ejecutiva en el catálogo y lo activa para la org"""
    db = _db()

    # 1. Verificar si ya existe en catálogo
    existing = db.table("adn_catalog").select("id").eq("slug", "secretaria-ejecutiva").execute()

    if existing.data:
        adn_id = existing.data[0]["id"]
    else:
        # 2. Crear en catálogo
        adn_data = {
            "slug": "secretaria-ejecutiva",
            "name": "Secretaria Ejecutiva",
            "role": "Súper Secretaria",
            "headline": "Mano derecha del ejecutivo, maestra de la organización",
            "bio": """Especialista en gestión ejecutiva con habilidades estratégicas y organizativas de nivel directivo.

HABILIDADES ESTRATÉGICAS:
• Proactividad y anticipación de necesidades
• Gestión experta de tiempo y agendas complejas
• Discreción rigurosa con información confidencial
• Adaptabilidad ante cambios y crisis

COMUNICACIÓN Y RELACIONES:
• Redacción ejecutiva modulable (formal, persuasivo, diplomático)
• Inteligencia emocional bajo presión
• Representación profesional corporativa

COMPETENCIAS TÉCNICAS:
• Dominio avanzado de Microsoft 365 y Google Workspace
• Organización de viajes nacionales e internacionales
• Gestión de proyectos y reportes ejecutivos
• Integración de IA en procesos administrativos

METODOLOGÍA:
• ReAct: Razonamiento antes de acción
• Chain of Thought: Desglosa problemas complejos paso a paso
• Plan-and-Solve: Ejecución ordenada de tareas

ESTÁNDARES:
• Cumplimiento GDPR y SOC 2
• Filtrado inteligente (urgente vs importante vs ruido)
• Manejo de contingencias sin alucinaciones
• Diplomacia automatizada en rechazos corporativos""",
            "skills": [
                "Gestión Ejecutiva",
                "Coordinación de Reuniones",
                "Redacción Ejecutiva",
                "Organización de Viajes",
                "Gestión de Calendarios",
                "Inteligencia Emocional",
                "Manejo de Crisis",
                "Privacidad de Datos",
                "Microsoft 365",
                "Google Workspace",
                "Análisis de Información",
                "Diplomacia Corporativa"
            ],
            "frameworks": [
                "ReAct",
                "Chain of Thought",
                "Plan-and-Solve",
                "GDPR Compliance",
                "SOC 2"
            ],
            "category": "directivo",
            "is_manager": True,
            "tier": "enterprise",
            "sort_order": 0  # Primera en la lista
        }

        res = db.table("adn_catalog").insert(adn_data).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Error creando ADN de Secretaria")
        adn_id = res.data[0]["id"]

    # 3. Verificar si ya está activada para esta org
    org_adn_check = (
        db.table("org_adn")
        .select("id")
        .eq("org_id", org_id)
        .eq("adn_id", adn_id)
        .execute()
    )

    if not org_adn_check.data:
        # 4. Activar para la org
        db.table("org_adn").insert({
            "org_id": org_id,
            "adn_id": adn_id,
            "status": "active"
        }).execute()

    return {
        "status": "ok",
        "message": "Secretaria Ejecutiva agregada al staff",
        "adn_id": adn_id
    }


# ── Stream ADN assignment ─────────────────────────────────────────────────────

@router.get("/stream/{stream_id}")
def list_stream_adns(stream_id: str, org_id: str = Depends(get_current_org)):
    """ADNs currently invited to a stream."""
    rows = (
        _db()
        .table("stream_adn")
        .select("id, is_lead, sort_order, invited_at, adn_catalog(id, slug, name, role, headline, avatar_url, category, is_manager)")
        .eq("stream_id", stream_id)
        .eq("org_id", org_id)
        .execute()
    )
    result = []
    for row in (rows.data or []):
        adn = row.get("adn_catalog") or {}
        adn["assignment_id"] = row["id"]
        adn["is_lead"] = row["is_lead"]
        adn["sort_order"] = row["sort_order"]
        adn["invited_at"] = row["invited_at"]
        result.append(adn)
    result.sort(key=lambda x: (0 if x["is_lead"] else 1, x.get("sort_order", 99)))
    return result


@router.post("/stream/{stream_id}/invite")
def invite_adn(stream_id: str, payload: StreamAdnPayload, org_id: str = Depends(get_current_org)):
    """Invite an ADN to a specific stream."""
    # Verify org has access to this ADN
    access = (
        _db()
        .table("org_adn")
        .select("id")
        .eq("org_id", org_id)
        .eq("adn_id", payload.adn_id)
        .eq("status", "active")
        .execute()
    )
    if not access.data:
        raise HTTPException(status_code=403, detail="ADN not available for this organization")

    row = (
        _db()
        .table("stream_adn")
        .upsert(
            {
                "org_id": org_id,
                "stream_id": stream_id,
                "adn_id": payload.adn_id,
                "is_lead": payload.is_lead,
                "sort_order": payload.sort_order,
            },
            on_conflict="stream_id,adn_id",
        )
        .execute()
    )
    return row.data[0] if row.data else {}


@router.delete("/stream/{stream_id}/uninvite/{adn_id}")
def uninvite_adn(stream_id: str, adn_id: str, org_id: str = Depends(get_current_org)):
    """Remove an ADN from a stream."""
    _db().table("stream_adn").delete().eq("stream_id", stream_id).eq("adn_id", adn_id).eq("org_id", org_id).execute()
    return {"ok": True}


@router.patch("/stream/{stream_id}/lead/{adn_id}")
def set_lead_adn(stream_id: str, adn_id: str, org_id: str = Depends(get_current_org)):
    """Mark one ADN as lead for the stream (clears previous lead)."""
    db = _db()
    # Clear all leads first
    db.table("stream_adn").update({"is_lead": False}).eq("stream_id", stream_id).eq("org_id", org_id).execute()
    # Set new lead
    db.table("stream_adn").update({"is_lead": True}).eq("stream_id", stream_id).eq("adn_id", adn_id).execute()
    return {"ok": True}


# ── Org hierarchy ─────────────────────────────────────────────────────────────

@router.get("/hierarchy")
def get_hierarchy(org_id: str = Depends(get_current_org)):
    """Return the hierarchy graph for the org as a list of edges."""
    rows = (
        _db()
        .table("adn_hierarchy")
        .select("id, manager_adn_id, report_adn_id")
        .eq("org_id", org_id)
        .execute()
    )
    return rows.data or []


@router.post("/hierarchy")
def add_hierarchy_edge(payload: HierarchyPayload, org_id: str = Depends(get_current_org)):
    row = (
        _db()
        .table("adn_hierarchy")
        .upsert(
            {"org_id": org_id, "manager_adn_id": payload.manager_adn_id, "report_adn_id": payload.report_adn_id},
            on_conflict="org_id,manager_adn_id,report_adn_id",
        )
        .execute()
    )
    return row.data[0] if row.data else {}


@router.delete("/hierarchy/{edge_id}")
def remove_hierarchy_edge(edge_id: str, org_id: str = Depends(get_current_org)):
    _db().table("adn_hierarchy").delete().eq("id", edge_id).eq("org_id", org_id).execute()
    return {"ok": True}


# ── System prompt builder (used by PromptAgent) ───────────────────────────────

@router.get("/stream/{stream_id}/system-prompt")
def build_stream_system_prompt(stream_id: str, org_id: str = Depends(get_current_org)):
    """Combine system prompts of all ADNs invited to a stream, with lead first."""
    rows = (
        _db()
        .table("stream_adn")
        .select("is_lead, sort_order, adn_catalog(slug, name, role, system_prompt)")
        .eq("stream_id", stream_id)
        .eq("org_id", org_id)
        .order("is_lead", desc=True)
        .execute()
    )

    if not rows.data:
        return {"system_prompt": "", "adns": []}

    parts = []
    adn_list = []
    for row in rows.data:
        adn = row.get("adn_catalog") or {}
        if not adn.get("system_prompt"):
            continue
        prefix = "## Tu rol principal" if row["is_lead"] else f"## Perspectiva adicional: {adn['name']}"
        parts.append(f"{prefix} — {adn['name']} ({adn['role']})\n\n{adn['system_prompt']}")
        adn_list.append({"slug": adn["slug"], "name": adn["name"], "is_lead": row["is_lead"]})

    combined = "\n\n---\n\n".join(parts)
    return {"system_prompt": combined, "adns": adn_list}
