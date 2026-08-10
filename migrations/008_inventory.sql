-- Migration 008: Inventario + Compras
-- Idempotente: usa IF NOT EXISTS y ON CONFLICT.

-- ── Productos / artículos de inventario ────────────────────────────────────────

create table if not exists inventory_items (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  product_id      uuid references erp_products(id) on delete set null,
  name            text not null,
  sku             text,
  description     text,
  unit            text default 'pza',
  current_stock   numeric default 0,
  min_stock       numeric default 0,
  reorder_point   numeric default 0,
  cost_price      numeric default 0,
  sale_price      numeric default 0,
  location        text,
  is_active       boolean default true,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now(),
  unique(org_id, sku)
);

-- ── Proveedores ──────────────────────────────────────────────────────────────

create table if not exists inventory_suppliers (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  name            text not null,
  contact_name    text,
  email           text,
  phone           text,
  tax_id          text,
  address         text,
  is_active       boolean default true,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- ── Órdenes de compra ───────────────────────────────────────────────────────

create table if not exists inventory_purchase_orders (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  supplier_id     uuid references inventory_suppliers(id) on delete set null,
  status          text default 'draft',  -- draft | sent | partial | received | cancelled
  order_date      date default current_date,
  expected_date   date,
  total           numeric default 0,
  notes           text,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- ── Items de órdenes de compra ─────────────────────────────────────────────────

create table if not exists inventory_purchase_order_items (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  order_id        uuid references inventory_purchase_orders(id) on delete cascade,
  item_id         uuid references inventory_items(id) on delete set null,
  quantity        numeric default 1,
  unit_cost       numeric default 0,
  total           numeric default 0,
  received_qty    numeric default 0,
  metadata        jsonb default '{}',
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- ── Movimientos de stock ───────────────────────────────────────────────────────

create table if not exists inventory_stock_movements (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  item_id         uuid references inventory_items(id) on delete cascade,
  type            text not null,           -- in | out | adjustment | return
  quantity        numeric not null default 0,
  reason          text,
  reference       text,
  related_po_id   uuid references inventory_purchase_orders(id) on delete set null,
  metadata        jsonb default '{}',
  created_at      timestamptz default now()
);

-- ── Índices ────────────────────────────────────────────────────────────────────

create index if not exists idx_inventory_items_org on inventory_items(org_id);
create index if not exists idx_inventory_items_active on inventory_items(org_id, is_active);
create index if not exists idx_inventory_suppliers_org on inventory_suppliers(org_id);
create index if not exists idx_inventory_po_org on inventory_purchase_orders(org_id);
create index if not exists idx_inventory_po_items_order on inventory_purchase_order_items(order_id);
create index if not exists idx_inventory_movements_item on inventory_stock_movements(item_id);
create index if not exists idx_inventory_movements_org on inventory_stock_movements(org_id);
