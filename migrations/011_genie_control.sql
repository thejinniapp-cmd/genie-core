-- Migration 011: Genie Control — Presupuesto vivo, KPIs, RRHH y dependencia del founder
-- Depende de Contabilidad Light (erp_accounts/erp_transactions) y del Workflow Engine.
-- Idempotente: usa IF NOT EXISTS y ON CONFLICT.

-- ── Presupuesto ─────────────────────────────────────────────────────────────────

create table if not exists erp_budgets (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  account_id      uuid references erp_accounts(id) on delete cascade,
  year            integer not null,
  month           integer not null check (month between 1 and 12),
  planned_amount  numeric not null default 0,
  notes           text,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now(),
  unique(org_id, account_id, year, month)
);

-- ── Rentabilidad por producto (falta costo en erp_products) ────────────────────

alter table erp_products add column if not exists cost_price numeric default 0;

-- ── RRHH ─────────────────────────────────────────────────────────────────────────

create table if not exists hr_employees (
  id                uuid primary key default gen_random_uuid(),
  org_id            uuid references organizations(id) on delete cascade,
  name              text not null,
  email             text,
  role_title        text,
  department        text,
  is_founder        boolean default false,
  is_manager        boolean default false,
  manager_id        uuid references hr_employees(id) on delete set null,
  status            text default 'active',   -- active | inactive
  hire_date         date,
  termination_date  date,
  metadata          jsonb default '{}',
  created_at        timestamptz default now(),
  updated_at        timestamptz default now(),
  unique(org_id, email)
);

-- Rotación, carga y dependencia del founder se derivan en tiempo de consulta contra
-- hr_employees (status/termination_date) y workflow_run_tasks (assigned_to/status/type)
-- que el Workflow Engine ya trackea — no se agregan tablas de eventos nuevas.

-- ── Índices ────────────────────────────────────────────────────────────────────

create index if not exists idx_erp_budgets_org on erp_budgets(org_id, year, month);
create index if not exists idx_erp_budgets_account on erp_budgets(account_id);
create index if not exists idx_hr_employees_org on hr_employees(org_id, status);
create index if not exists idx_hr_employees_manager on hr_employees(manager_id);
create index if not exists idx_hr_employees_department on hr_employees(org_id, department);

-- ── RLS (service role bypassa; sin policies explícitas, igual que 010) ─────────

alter table erp_budgets enable row level security;
alter table hr_employees enable row level security;

-- ── Seed: registro en el Marketplace ────────────────────────────────────────────

insert into marketplace_items (slug, name, description, item_type, category, is_public, price_monthly, dependencies, connectors_required, metadata)
values
  ('control', 'Genie Control', 'Presupuesto vivo, dashboard de KPIs, RRHH y detección de dependencia del founder', 'app', 'operations', true, 0, array['accounting'], array[]::text[], '{}'),
  ('budget_agent', 'Agente de Presupuesto', 'Compara presupuesto vs. real cada semana y explica las desviaciones', 'ai_staff', 'finance', true, 0, array['control'], array[]::text[], '{}')
on conflict (slug) do update set
  name = excluded.name,
  description = excluded.description,
  category = excluded.category,
  dependencies = excluded.dependencies;
