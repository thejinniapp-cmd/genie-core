-- Migration 013: Genie Cumple — Calendario fiscal, auditoría de CFDI, simulador de régimen
-- Idempotente: usa IF NOT EXISTS.

-- ── Identidad fiscal de la organización (una fila por org) ─────────────────────

create table if not exists fiscal_profile (
  org_id           uuid primary key references organizations(id) on delete cascade,
  rfc              text,
  razon_social     text,
  regimen_fiscal   text,           -- texto libre: 'RESICO', 'RIF', 'Régimen General PM', etc.
  regimen_since    date,
  metadata         jsonb default '{}',
  updated_at       timestamptz default now()
);

-- ── Reglas base del calendario fiscal (qué obligaciones existen) ───────────────

create table if not exists fiscal_obligations (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  name            text not null,
  obligation_type text not null,                    -- isr_mensual | iva_mensual | declaracion_anual | otro
  frequency       text not null default 'monthly',   -- monthly | annual
  due_day         integer not null default 17,
  due_month       integer,                            -- solo para frequency=annual
  months_offset   integer default 1,                  -- 1 = se vence al mes siguiente del período
  is_active       boolean default true,
  notes           text,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- ── Instancias concretas por período (lo que se ve en el calendario) ───────────

create table if not exists fiscal_obligation_instances (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  obligation_id   uuid references fiscal_obligations(id) on delete cascade,
  year            integer not null,
  month           integer not null,
  due_date        date not null,
  status          text default 'pending',   -- pending | prepared | filed
  draft_amount    numeric,
  notes           text,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now(),
  unique(org_id, obligation_id, year, month)
);

-- ── CFDI importados (vía XML pegado o carga manual) ────────────────────────────

create table if not exists cfdi_documents (
  id                uuid primary key default gen_random_uuid(),
  org_id            uuid references organizations(id) on delete cascade,
  uuid_fiscal       text not null,
  tipo_comprobante  text,              -- I=ingreso, E=egreso, P=pago, N=nómina, T=traslado
  rfc_emisor        text,
  nombre_emisor     text,
  rfc_receptor      text,
  nombre_receptor   text,
  fecha_emision     date,
  subtotal          numeric default 0,
  iva               numeric default 0,
  total             numeric default 0,
  moneda            text default 'MXN',
  status            text default 'vigente',    -- vigente | cancelado
  is_efos_flagged   boolean default false,      -- marcado manualmente hasta tener integración con lista 69-B del SAT
  source            text default 'manual',      -- xml_paste | manual
  raw_xml           text,
  metadata          jsonb default '{}',
  created_at        timestamptz default now(),
  unique(org_id, uuid_fiscal)
);

-- ── Índices ────────────────────────────────────────────────────────────────────

create index if not exists idx_fiscal_obligation_instances_org on fiscal_obligation_instances(org_id, due_date);
create index if not exists idx_cfdi_documents_org on cfdi_documents(org_id, fecha_emision);
create index if not exists idx_cfdi_documents_rfc_emisor on cfdi_documents(org_id, rfc_emisor);

-- ── RLS (service role bypassa; sin policies explícitas, igual que 010/011/012) ─

alter table fiscal_profile enable row level security;
alter table fiscal_obligations enable row level security;
alter table fiscal_obligation_instances enable row level security;
alter table cfdi_documents enable row level security;

-- ── Seed: registro en el Marketplace ────────────────────────────────────────────

insert into marketplace_items (slug, name, description, item_type, category, is_public, price_monthly, dependencies, connectors_required, metadata)
values
  ('cumple', 'Genie Cumple', 'Calendario fiscal, auditoría de CFDI y simulador de régimen', 'app', 'finance', true, 0, array['accounting'], array[]::text[], '{}'),
  ('compliance_agent', 'Agente de Cumplimiento SAT', 'Vigila fechas fiscales y audita CFDI importados', 'ai_staff', 'finance', true, 0, array['cumple'], array[]::text[], '{}')
on conflict (slug) do update set
  name = excluded.name,
  description = excluded.description,
  category = excluded.category,
  dependencies = excluded.dependencies;
