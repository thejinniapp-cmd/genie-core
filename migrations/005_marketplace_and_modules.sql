-- Migration 005: Marketplace de apps, planes y CRM base
-- Modelo de producto: Genie Base + Genie Apps + Paquetes
-- Idempotente: usa IF NOT EXISTS y DO $$ para alteraciones seguras.

-- ── Planes y suscripción ──────────────────────────────────────────────────────

create table if not exists plans (
  id              uuid primary key default gen_random_uuid(),
  slug            text unique not null,   -- starter | pyme | enterprise
  name            text not null,
  description     text,
  price_monthly   numeric default 0,
  is_public       boolean default true,
  included_modules text[] default '{}',
  included_ai_staff text[] default '{}',
  included_connectors text[] default '{}',
  limits          jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

create table if not exists organization_subscriptions (
  id                  uuid primary key default gen_random_uuid(),
  organization_id     uuid references organizations(id) on delete cascade,
  plan_id             uuid references plans(id),
  status              text default 'active',  -- trial | active | past_due | cancelled
  trial_ends_at       timestamptz,
  current_period_starts_at timestamptz default now(),
  current_period_ends_at timestamptz,
  metadata            jsonb default '{}',
  created_at          timestamptz default now(),
  updated_at          timestamptz default now(),
  unique(organization_id)
);

create table if not exists organization_modules (
  organization_id     uuid references organizations(id) on delete cascade,
  module_key          text not null,
  source              text default 'plan',  -- plan | purchased | trial
  enabled             boolean default false,
  config              jsonb default '{}',
  enabled_at          timestamptz,
  disabled_at         timestamptz,
  created_at          timestamptz default now(),
  updated_at          timestamptz default now(),
  primary key (organization_id, module_key)
);

create table if not exists organization_ai_staff (
  organization_id     uuid references organizations(id) on delete cascade,
  staff_key           text not null,
  source              text default 'plan',
  enabled             boolean default false,
  config              jsonb default '{}',
  enabled_at          timestamptz,
  primary key (organization_id, staff_key)
);

-- ── Catálogo del Marketplace ───────────────────────────────────────────────────

create table if not exists marketplace_items (
  id              uuid primary key default gen_random_uuid(),
  slug            text unique not null,   -- crm | cobranza | collector_agent | hubspot_connector
  name            text not null,
  description     text,
  item_type       text not null,          -- app | ai_staff | connector | skill
  category        text,                   -- crm | erp | finance | sales | channel
  is_public       boolean default true,
  is_official     boolean default true,
  price_monthly   numeric default 0,
  dependencies    text[] default '{}',    -- slugs de otros items requeridos
  connectors_required text[] default '{}',
  config_schema   jsonb default '{}',
  metadata        jsonb default '{}',
  icon_url        text,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- ── CRM base ───────────────────────────────────────────────────────────────────

-- Empresas/clientes

create table if not exists crm_companies (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  name            text not null,
  legal_name      text,
  tax_id          text,
  industry        text,
  website         text,
  phone           text,
  email           text,
  address         text,
  city            text,
  country         text default 'MX',
  status          text default 'active',  -- active | inactive | prospect
  source          text,                   -- manual | hubspot | pipedrive | import
  external_id     text,                   -- id en sistema externo
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- Contactos (personas asociadas a empresas)

create table if not exists crm_contacts (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  company_id      uuid references crm_companies(id) on delete set null,
  first_name      text not null,
  last_name       text,
  email           text,
  phone           text,
  job_title       text,
  is_primary      boolean default false,
  status          text default 'active',
  source          text,
  external_id     text,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- Pipelines y etapas de ventas

create table if not exists crm_pipelines (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  name            text not null,
  is_default      boolean default false,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now(),
  unique(org_id, name)
);

create table if not exists crm_stages (
  id              uuid primary key default gen_random_uuid(),
  pipeline_id     uuid references crm_pipelines(id) on delete cascade,
  org_id          uuid references organizations(id) on delete cascade,
  name            text not null,
  order_index     integer default 0,
  win_probability numeric default 0,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- Oportunidades / deals

create table if not exists crm_deals (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  pipeline_id     uuid references crm_pipelines(id) on delete set null,
  stage_id        uuid references crm_stages(id) on delete set null,
  company_id      uuid references crm_companies(id) on delete set null,
  contact_id      uuid references crm_contacts(id) on delete set null,
  name            text not null,
  value           numeric default 0,
  currency        text default 'MXN',
  expected_close_date date,
  status          text default 'open',  -- open | won | lost | paused
  source          text,
  owner_id        uuid references users(id) on delete set null,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- Actividades (llamadas, emails, reuniones, notas)

create table if not exists crm_activities (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  deal_id         uuid references crm_deals(id) on delete cascade,
  company_id      uuid references crm_companies(id) on delete set null,
  contact_id      uuid references crm_contacts(id) on delete set null,
  activity_type   text not null,          -- call | email | meeting | note | task | whatsapp
  subject         text,
  notes           text,
  scheduled_at    timestamptz,
  completed_at    timestamptz,
  status          text default 'pending', -- pending | completed | cancelled
  owner_id        uuid references users(id) on delete set null,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- ── Índices ────────────────────────────────────────────────────────────────────

create index if not exists idx_organization_subscriptions_org on organization_subscriptions(organization_id);
create index if not exists idx_organization_modules_org on organization_modules(organization_id);
create index if not exists idx_organization_ai_staff_org on organization_ai_staff(organization_id);
create index if not exists idx_marketplace_items_type on marketplace_items(item_type);
create index if not exists idx_marketplace_items_category on marketplace_items(category);
create index if not exists idx_crm_companies_org on crm_companies(org_id);
create index if not exists idx_crm_contacts_org on crm_contacts(org_id);
create index if not exists idx_crm_contacts_company on crm_contacts(company_id);
create index if not exists idx_crm_deals_org on crm_deals(org_id);
create index if not exists idx_crm_deals_stage on crm_deals(stage_id);
create index if not exists idx_crm_activities_deal on crm_activities(deal_id);
create index if not exists idx_crm_activities_scheduled on crm_activities(org_id, scheduled_at);

-- ── Seed: planes y catálogo base ───────────────────────────────────────────────

insert into plans (slug, name, description, included_modules, included_ai_staff, included_connectors, limits)
values
  ('starter', 'Genie Starter', 'Base + AI Staff esencial para empezar.', '{}', '{"chief_of_staff"}', '{"google_workspace"}', '{"max_agents": 3, "max_streams": 3}'),
  ('pyme', 'Genie PyME', 'Base + CRM/Ventas + Cobranza y staff extendido.', '{"crm", "collections"}', '{"chief_of_staff", "sales_agent", "collector_agent"}', '{"google_workspace", "stripe", "hubspot", "pipedrive"}', '{"max_agents": 10, "max_streams": 10}'),
  ('enterprise', 'Genie Enterprise', 'Base + todas las apps + custom connectors + soporte.', '{"crm", "collections", "accounting", "inventory"}', '{}', '{}', '{"max_agents": 9999, "max_streams": 9999}')
on conflict (slug) do update set
  name = excluded.name,
  description = excluded.description,
  included_modules = excluded.included_modules,
  included_ai_staff = excluded.included_ai_staff,
  included_connectors = excluded.included_connectors,
  limits = excluded.limits,
  updated_at = now();

insert into marketplace_items (slug, name, description, item_type, category, is_public, price_monthly, dependencies, connectors_required, metadata)
values
  ('crm', 'CRM / Ventas', 'Contactos, empresas, pipeline de oportunidades y actividades.', 'app', 'crm', true, 29, '{}', '{}', '{"icon": "Users", "tagline": "Tu agente de ventas nunca olvida seguir a un cliente."}'),
  ('collections', 'Cobranza + Facturación', 'Facturas, cuentas por cobrar y recordatorios automáticos.', 'app', 'finance', true, 39, '{"crm"}', '{"stripe"}', '{"icon": "Banknote", "tagline": "Recupera pagos atrasados sin perder tiempo."}'),
  ('accounting', 'Contabilidad Light', 'Gastos, ingresos y reportes fiscales simplificados.', 'app', 'finance', true, 19, '{"crm", "collections"}', '{}', '{"icon": "Calculator", "tagline": "Mantén tus finanzas ordenadas para el contador."}'),
  ('inventory', 'Inventario + Compras', 'Productos, stock y órdenes de compra.', 'app', 'erp', true, 29, '{"crm"}', '{}', '{"icon": "Package", "tagline": "Saber qué tienes y qué necesitas comprar."}'),
  ('sales_agent', 'Agente de Ventas', 'Sugiere seguimientos, redacta correos y resume historial.', 'ai_staff', 'sales', true, 0, '{"crm"}', '{}', '{"icon": "Bot", "tagline": "Cierra más deals con seguimiento oportuno."}'),
  ('collector_agent', 'Agente Cobrador', 'Detecta facturas vencidas y envía recordatorios.', 'ai_staff', 'finance', true, 0, '{"collections"}', '{}', '{"icon": "Banknote", "tagline": "Cobra sin ser grosero ni olvidadizo."}'),
  ('hubspot_connector', 'HubSpot', 'Sincroniza contactos, empresas y deals con HubSpot.', 'connector', 'crm', true, 0, '{}', '{}', '{"icon": "Hubspot", "auth_type": "oauth2"}'),
  ('pipedrive_connector', 'Pipedrive', 'Sincroniza tu pipeline de ventas con Pipedrive.', 'connector', 'crm', true, 0, '{}', '{}', '{"icon": "Pipedrive", "auth_type": "oauth2"}'),
  ('odoo_connector', 'Odoo', 'Conecta inventario, facturación y CRM de Odoo.', 'connector', 'erp', true, 0, '{}', '{}', '{"icon": "Odoo", "auth_type": "api_key"}')
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
