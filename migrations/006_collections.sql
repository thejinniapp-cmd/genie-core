-- Migration 006: Cobranza + Facturación
-- Depende de CRM (contactos y empresas) y comparte org_id.

create table if not exists erp_products (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  name            text not null,
  description     text,
  sku             text,
  price           numeric default 0,
  currency        text default 'MXN',
  stock           numeric default 0,
  is_active       boolean default true,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now(),
  unique(org_id, sku)
);

create table if not exists erp_invoices (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  contact_id      uuid references crm_contacts(id) on delete set null,
  company_id      uuid references crm_companies(id) on delete set null,
  invoice_number  text not null,
  status          text default 'draft',  -- draft | sent | paid | overdue | cancelled
  issue_date      date default current_date,
  due_date        date,
  subtotal        numeric default 0,
  tax_rate        numeric default 0.16,
  tax_amount      numeric default 0,
  discount        numeric default 0,
  total           numeric default 0,
  currency        text default 'MXN',
  notes           text,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

create table if not exists erp_invoice_items (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  invoice_id      uuid references erp_invoices(id) on delete cascade,
  product_id      uuid references erp_products(id) on delete set null,
  description     text not null,
  quantity        numeric default 1,
  unit_price      numeric default 0,
  amount          numeric default 0,
  metadata        jsonb default '{}',
  created_at      timestamptz default now()
);

create table if not exists erp_payments (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  invoice_id      uuid references erp_invoices(id) on delete cascade,
  amount          numeric not null,
  payment_date    date default current_date,
  method          text default 'transfer',  -- cash | card | transfer | check | other
  reference       text,
  notes           text,
  metadata        jsonb default '{}',
  created_at      timestamptz default now()
);

create index if not exists idx_erp_products_org on erp_products(org_id);
create index if not exists idx_erp_invoices_org on erp_invoices(org_id);
create index if not exists idx_erp_invoices_status on erp_invoices(status);
create index if not exists idx_erp_invoices_due_date on erp_invoices(due_date);
create index if not exists idx_erp_invoice_items_invoice on erp_invoice_items(invoice_id);
create index if not exists idx_erp_payments_invoice on erp_payments(invoice_id);
create index if not exists idx_erp_payments_payment_date on erp_payments(payment_date);
