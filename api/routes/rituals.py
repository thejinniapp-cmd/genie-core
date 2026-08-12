"""api/routes/rituals.py — Ritual Engine para comunicación proactiva del Chief of Staff.

Expone los 5 rituales del Executive Briefing (pantallas 12a-e) con el contenido
de referencia del handoff. En producción este fixture se reemplaza por lectura
real de workflows, agentes y notificaciones.
"""
from fastapi import APIRouter, Depends

from api.auth import get_current_org

router = APIRouter()


_SAMPLE_DAILY_RITUAL = {
    "context": {
        "label": "RFQ · MRO Master",
        "status": "Empresa saludable",
        "status_color": "green",
    },
    "rituals": {
        "morning_brief": {
            "greeting": "Buenos días",
            "date": "Lunes, 09:02",
            "narrative": "Buenos días, Gabriel. La empresa está estable. Hay **una decisión estratégica que deberíamos tomar antes de las 11**, un riesgo operativo que ya estoy mitigando, y **una oportunidad de ahorro** que vale la pena revisar. Todo lo demás está bajo control.",
            "items": [
                {
                    "category": "DECISION",
                    "title": "Contrato con Distribuidora Industrial MX — vence hoy 11:00 AM",
                    "link": "Ir a Focus Queue →",
                },
                {
                    "category": "RIESGO",
                    "title": "Retraso Fornecedor Sul Ltda — ya reasignado a MRO Global, sin acción tuya requerida",
                    "link": "Ir a Focus Queue →",
                },
                {
                    "category": "OPORTUNIDAD",
                    "title": "$180K subutilizados en Marketing — reasignación sugerida por el CFO",
                    "link": "Ir a Focus Queue →",
                },
            ],
            "overnight_metrics": [
                {"value": "14", "label": "órdenes procesadas"},
                {"value": "8", "label": "incidencias resueltas"},
                {"value": "4", "label": "clientes atendidos"},
                {"value": "2", "label": "aprobaciones automáticas"},
            ],
            "weekly_summary": {
                "decisions": 3,
                "risks": 1,
                "opportunity": "$180K oportunidad",
            },
        },
        "focus_queue": {
            "items": [
                {
                    "id": "focus:decision",
                    "category": "DECISION",
                    "status": "VENCE HOY 11:00 AM",
                    "source": "Sourcing",
                    "time": "hace 2h",
                    "title": "Contrato con Distribuidora Industrial MX",
                    "reasoning": "Recomiendo este proveedor porque reduce el costo un 12%, entrega en 4 días en vez de 9, y tiene 0 incidencias en 14 órdenes previas. Es la opción de menor riesgo de las 3 que comparé.",
                    "actions": [
                        {"label": "✓ Aprobar", "style": "primary"},
                        {"label": "Delegar", "style": "secondary"},
                        {"label": "Pedir otra opción", "style": "secondary"},
                        {"label": "Ver análisis completo", "style": "secondary"},
                        {"label": "Posponer", "style": "muted"},
                    ],
                },
                {
                    "id": "focus:risk",
                    "category": "RIESGO",
                    "status": "YA MITIGADO",
                    "source": "Sourcing",
                    "time": "hace 1h",
                    "title": "Retraso Fornecedor Sul Ltda",
                    "reasoning": "Detecté un retraso de Fornecedor Sul Ltda que afectaba el 18% de la producción del viernes — ya reasigné el batch #4471-B a MRO Global de México, que entrega en 2 días. No requiere tu acción, solo tu visto bueno.",
                    "actions": [
                        {"label": "Confirmar", "style": "secondary"},
                        {"label": "Revertir", "style": "muted"},
                    ],
                },
                {
                    "id": "focus:opportunity",
                    "category": "OPORTUNIDAD",
                    "status": "CFO",
                    "source": "Finanzas",
                    "time": "hace 37m",
                    "title": "$180K subutilizados en Marketing",
                    "reasoning": "Encontré esta oportunidad porque el ritmo de gasto de Marketing en Q3 deja $180K sin usar antes del cierre — reasignarlo a Sourcing aceleraría 2 RFQs que están esperando presupuesto.",
                    "actions": [
                        {"label": "Reasignar presupuesto", "style": "secondary"},
                        {"label": "Simular otro escenario", "style": "muted"},
                        {"label": "Descartar", "style": "muted"},
                    ],
                },
            ],
        },
        "live_operations": {
            "intro": "No es el log completo — solo lo que cambia el estado del negocio. El resto vive en Memoria organizacional.",
            "events": [
                {
                    "dot_color": "green",
                    "time": "09:41",
                    "title": "MRO Global de México confirmó entrega del batch #4471-B — riesgo resuelto",
                    "action": "Ver en Focus Queue",
                },
                {
                    "dot_color": "blue",
                    "time": "09:22",
                    "title": "Nueva RFQ entrante — Componentes hidráulicos, cliente recurrente",
                    "action": "Revisar",
                },
                {
                    "dot_color": "orange",
                    "time": "08:55",
                    "title": "Agente Legal marcó una cláusula de penalización fuera de rango en el contrato de Sourcing",
                    "action": "Ver detalle",
                },
            ],
            "footer": "Actualizaciones rutinarias (jobs completados, sincronizaciones, etc.) no aparecen aquí — están en Memoria organizacional si las necesitas.",
        },
        "daily_wrapup": {
            "date": "Lunes, 19:04",
            "narrative": "Cerraste el día con la operación estable. Tomaste la decisión del proveedor a tiempo, y el riesgo que detecté esta mañana quedó resuelto sin más incidentes.",
            "sections": [
                {
                    "title": "Qué ocurrió",
                    "color": "green",
                    "icon": "✓",
                    "items": [
                        "Aprobaste el contrato con Distribuidora Industrial MX a las 10:20 AM",
                        "El riesgo de Fornecedor Sul Ltda se resolvió sin afectar producción",
                        "16 órdenes procesadas, 9 incidencias resueltas por agentes",
                    ],
                },
                {
                    "title": "Qué quedó pendiente",
                    "color": "orange",
                    "icon": "→",
                    "items": ["La reasignación de presupuesto Q3 del CFO — sigue esperando tu decisión"],
                },
                {
                    "title": "Qué aprendí hoy",
                    "color": "blue",
                    "icon": "◈",
                    "items": ["Fornecedor Sul Ltda acumula 2 retrasos este mes — voy a bajar su prioridad en la próxima ronda de cotizaciones."],
                },
                {
                    "title": "Para mañana recomiendo",
                    "color": "purple",
                    "icon": "✦",
                    "items": ["Resolver la reasignación de presupuesto antes del mediodía — dos RFQs de Sourcing dependen de eso para avanzar esta semana."],
                },
            ],
        },
        "weekly_review": {
            "week_range": "Semana del 29 jun – 5 jul",
            "narrative": "Fue una semana sólida — el equipo y los agentes mantuvieron la operación estable mientras se destrabó una decisión de sourcing importante. Un riesgo de proveedor apareció y se resolvió sin impacto real.",
            "sections": [
                {
                    "title": "Qué mejoró",
                    "color": "green",
                    "icon": "▲",
                    "items": ["El tiempo promedio de aprobación de RFQs bajó de 2.1 a 1.4 días — la Focus Queue está ayudando a decidir más rápido."],
                },
                {
                    "title": "Qué empeoró",
                    "color": "orange",
                    "icon": "▼",
                    "items": ["Fornecedor Sul Ltda acumula su segundo retraso del mes — vale la pena reconsiderar su lugar en el panel de proveedores preferidos."],
                },
                {
                    "title": "Oportunidades que encontré",
                    "color": "blue",
                    "icon": "✦",
                    "items": ["$180K subutilizados en Marketing Q3 que podrían acelerar 2 RFQs de Sourcing — sigue pendiente tu decisión."],
                },
                {
                    "title": "Para la próxima semana recomiendo",
                    "color": "purple",
                    "icon": "→",
                    "items": ["Resolver la reasignación de presupuesto y evaluar un proveedor alternativo permanente para reemplazar a Fornecedor Sul Ltda en la categoría de componentes hidráulicos."],
                },
            ],
        },
    },
}


@router.get("/daily")
def get_daily_ritual(org_id: str = Depends(get_current_org)):
    """Devuelve el fixture de rituales ejecutivos del handoff (12a-e)."""
    return _SAMPLE_DAILY_RITUAL
