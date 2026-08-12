-- Migration 012: Genie Caja — Forecast de 13 semanas, concentración de clientes, cobranza proactiva
-- Vive bajo el module_key 'collections' ya registrado en la migración 005.
-- Idempotente: usa IF NOT EXISTS.

-- ── Compromisos recurrentes (salidas/entradas predecibles para el forecast) ────

create table if not exists erp_recurring_commitments (
  id            uuid primary key default gen_random_uuid(),
  org_id        uuid references organizations(id) on delete cascade,
  name          text not null,
  type          text not null,                    -- income | expense
  amount        numeric not null default 0,
  frequency     text not null default 'monthly',   -- weekly | biweekly | monthly
  day_of_month  integer,                            -- para frequency=monthly (1-28)
  weekday       integer,                            -- para frequency=weekly/biweekly (0=lunes)
  account_id    uuid references erp_accounts(id) on delete set null,
  is_active     boolean default true,
  notes         text,
  metadata      jsonb default '{}',
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

-- ── Checkpoints manuales de saldo (sin Open Banking, el dueño los captura) ─────

create table if not exists erp_cash_balance_snapshots (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid references organizations(id) on delete cascade,
  balance     numeric not null,
  as_of_date  date not null default current_date,
  notes       text,
  created_at  timestamptz default now()
);

-- ── Índices ────────────────────────────────────────────────────────────────────

create index if not exists idx_erp_recurring_commitments_org on erp_recurring_commitments(org_id, is_active);
create index if not exists idx_erp_cash_snapshots_org on erp_cash_balance_snapshots(org_id, as_of_date desc);

-- ── RLS (service role bypassa; sin policies explícitas, igual que 010/011) ─────

alter table erp_recurring_commitments enable row level security;
alter table erp_cash_balance_snapshots enable row level security;
