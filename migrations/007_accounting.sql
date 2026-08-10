-- Migration 007: Contabilidad Light
-- Módulo de gastos, ingresos y resumen fiscal simplificado.
-- Idempotente: usa IF NOT EXISTS y ON CONFLICT.

-- ── Cuentas / categorías contables ─────────────────────────────────────────────

create table if not exists erp_accounts (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  name            text not null,
  type            text not null,               -- income | expense | asset | liability
  color           text default '#7F77DD',
  description     text,
  is_active       boolean default true,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now(),
  unique(org_id, name, type)
);

-- ── Transacciones (ingresos y gastos) ──────────────────────────────────────────

create table if not exists erp_transactions (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  account_id      uuid references erp_accounts(id) on delete set null,
  type            text not null,               -- income | expense | transfer
  amount          numeric not null default 0,
  currency        text default 'MXN',
  transaction_date date not null default current_date,
  description     text,
  reference       text,                       -- número de factura, recibo, etc.
  contact_id      uuid references crm_contacts(id) on delete set null,
  invoice_id        uuid references erp_invoices(id) on delete set null,
  is_recurring    boolean default false,
  recurrence_rule jsonb default '{}',
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- ── Períodos fiscales simples ──────────────────────────────────────────────────

create table if not exists accounting_tax_periods (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  year            integer not null,
  month           integer not null check (month between 1 and 12),
  status          text default 'open',         -- open | closed | declared
  notes           text,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now(),
  unique(org_id, year, month)
);

-- ── Índices ────────────────────────────────────────────────────────────────────

create index if not exists idx_erp_accounts_org on erp_accounts(org_id);
create index if not exists idx_erp_transactions_org on erp_transactions(org_id);
create index if not exists idx_erp_transactions_date on erp_transactions(org_id, transaction_date);
create index if not exists idx_erp_transactions_account on erp_transactions(account_id);
create index if not exists idx_accounting_tax_periods_org on accounting_tax_periods(org_id, year, month);

-- ── Seed: categorías oficiales por defecto ─────────────────────────────────────
-- Se insertan solo cuando una org activa el módulo, pero dejamos un seed común.

-- (El marketplace_items de 'accounting' ya fue insertado en migración 005.)
