-- Migration 009: AI Staff y conectores oficiales en el marketplace
-- Idempotente: usa ON CONFLICT (slug) para no duplicar.

insert into marketplace_items (slug, name, description, item_type, category, is_public, is_official, price_monthly, dependencies, connectors_required, metadata)
values
  ('chief_of_staff', 'Chief of Staff', 'Coordina tareas, recordatorios y resúmenes para el equipo.', 'ai_staff', 'ops', true, true, 0, '{}', '{}', '{"icon": "Crown", "tagline": "Tu asistente ejecutivo invisible."}'),
  ('sales_agent', 'Agente de Ventas', 'Sugiere seguimientos, redacta correos y resume historial.', 'ai_staff', 'sales', true, true, 0, '{"crm"}', '{}', '{"icon": "TrendingUp", "tagline": "Cierra más deals con seguimiento oportuno."}'),
  ('collector_agent', 'Agente Cobrador', 'Detecta facturas vencidas y envía recordatorios.', 'ai_staff', 'finance', true, true, 0, '{"collections"}', '{}', '{"icon": "Banknote", "tagline": "Cobra sin ser grosero ni olvidadizo."}'),
  ('hubspot_connector', 'HubSpot', 'Sincroniza contactos, empresas y deals con HubSpot.', 'connector', 'crm', true, true, 0, '{}', '{}', '{"icon": "Hubspot", "auth_type": "oauth2"}'),
  ('pipedrive_connector', 'Pipedrive', 'Sincroniza tu pipeline de ventas con Pipedrive.', 'connector', 'crm', true, true, 0, '{}', '{}', '{"icon": "Pipedrive", "auth_type": "oauth2"}'),
  ('odoo_connector', 'Odoo', 'Conecta inventario, facturación y CRM de Odoo.', 'connector', 'erp', true, true, 0, '{}', '{}', '{"icon": "Odoo", "auth_type": "api_key"}')
on conflict (slug) do update set
  name = excluded.name,
  description = excluded.description,
  item_type = excluded.item_type,
  category = excluded.category,
  price_monthly = excluded.price_monthly,
  dependencies = excluded.dependencies,
  connectors_required = excluded.connectors_required,
  metadata = excluded.metadata,
  updated_at = now();
