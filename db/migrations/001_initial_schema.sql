-- ============================================================
-- Genie Core — Schema v1
-- Ejecutar en Supabase SQL Editor
-- ============================================================

-- Habilitar extensiones necesarias
create extension if not exists "uuid-ossp";
create extension if not exists "vector";
create extension if not exists "pg_trgm";

-- ── Organizaciones ────────────────────────────────────────────────────────────

create table organizations (
  id           uuid primary key default gen_random_uuid(),
  name         text not null,
  slug         text unique not null,
  logo_url     text,
  plan         text default 'starter',  -- starter | pro | enterprise
  status       text default 'active',
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);

create table org_config (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  default_model   text default 'anthropic/claude-sonnet-4-5',
  fast_model      text default 'anthropic/claude-haiku-4-5',
  autonomy_global text default 'supervised',  -- manual | supervised | autonomous
  timezone        text default 'America/Mexico_City',
  language        text default 'es',
  settings        jsonb default '{}',
  created_at      timestamptz default now(),
  unique(org_id)
);

-- ── Users ─────────────────────────────────────────────────────────────────────

create table users (
  id           uuid primary key default gen_random_uuid(),
  email        text unique not null,
  name         text,
  avatar_url   text,
  created_at   timestamptz default now()
);

create table org_members (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid references organizations(id) on delete cascade,
  user_id        uuid references users(id) on delete cascade,
  role           text default 'member',  -- owner | admin | member | viewer
  permissions    text[] default '{}',    -- ['approve_rfq', 'configure_agents', ...]
  stream_access  uuid[] default '{}',    -- streams accesibles (vacío = todos)
  created_at     timestamptz default now(),
  unique(org_id, user_id)
);

-- Usuarios externos (clientes, proveedores, etc.)
create table external_users (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid references organizations(id) on delete cascade,
  email        text,
  name         text,
  type         text default 'client',   -- client | supplier | prospect | partner
  phone        text,
  metadata     jsonb default '{}',
  created_at   timestamptz default now()
);

-- ── Infraestructura ───────────────────────────────────────────────────────────

create table org_infra (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  railway_token   text,                  -- cifrado
  railway_project text,
  supabase_url    text,
  supabase_key    text,                  -- cifrado
  use_genie_infra boolean default true,  -- true = usar infra de Genie
  created_at      timestamptz default now(),
  unique(org_id)
);

-- Infra específica por stream (override de org_infra)
create table stream_infra (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid references organizations(id) on delete cascade,
  stream_id   uuid,                      -- FK se agrega después
  infra_type  text,                      -- railway | supabase | hostinger
  config      jsonb default '{}',
  created_at  timestamptz default now()
);

-- ── Conectores ────────────────────────────────────────────────────────────────

create table connectors (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  connector_type  text not null,         -- gmail | google_drive | 1crm | telegram | ...
  status          text default 'disconnected',  -- connected | disconnected | error
  credentials     jsonb default '{}',    -- cifrado con Supabase Vault
  config          jsonb default '{}',    -- configuración adicional no sensible
  last_tested_at  timestamptz,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now(),
  unique(org_id, connector_type)
);

-- ── Streams ───────────────────────────────────────────────────────────────────

create table streams (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid references organizations(id) on delete cascade,
  name        text not null,
  description text,
  type        text default 'general',    -- general | sales | support | ops | custom
  status      text default 'active',
  config      jsonb default '{}',
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);

-- Conexiones entre streams (macroflujo)
create table stream_connections (
  id                uuid primary key default gen_random_uuid(),
  org_id            uuid references organizations(id),
  stream_origen_id  uuid references streams(id),
  stream_destino_id uuid references streams(id),
  output_field      text,
  input_field       text,
  order_index       integer default 0,
  created_at        timestamptz default now()
);

-- Notificaciones de stream (aprobaciones, alertas)
create table stream_notifications (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid references organizations(id) on delete cascade,
  stream_id   uuid references streams(id),
  job_id      uuid,
  user_id     uuid references users(id),
  type        text,   -- approval_required | alert | info
  message     text,
  status      text default 'pending',  -- pending | approved | rejected | dismissed
  metadata    jsonb default '{}',
  created_at  timestamptz default now(),
  resolved_at timestamptz
);

-- ── Agentes ───────────────────────────────────────────────────────────────────

create table agents (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  stream_id       uuid references streams(id),
  name            text not null,
  description     text,
  agent_type      text default 'prompt',  -- prompt | worker | bot
  model_id        text,
  system_prompt   text,
  temperature     numeric default 0.3,
  max_tokens      integer default 2048,
  tools           text[] default '{}',    -- conectores disponibles
  autonomy_level  text default 'supervised',
  is_active       boolean default true,
  config          jsonb default '{}',
  skill_id        uuid,                   -- si viene de un skill del marketplace
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- ── Jobs ─────────────────────────────────────────────────────────────────────

create table jobs (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid references organizations(id) on delete cascade,
  stream_id    uuid references streams(id),
  agent_id     uuid references agents(id),
  agent_type   text not null,
  status       text default 'pending',
  -- pending | running | completed | failed | waiting_approval | skipped
  input_data   jsonb default '{}',
  output       jsonb,
  error        text,
  logs         jsonb[] default '{}',
  attempt      integer default 1,
  priority     integer default 0,
  agent_config jsonb default '{}',
  created_at   timestamptz default now(),
  started_at   timestamptz,
  finished_at  timestamptz
);

create index idx_jobs_status_type on jobs(agent_type, status);
create index idx_jobs_org_stream on jobs(org_id, stream_id);

-- ── Mensajes del stream ───────────────────────────────────────────────────────

create table messages (
  id         uuid primary key default gen_random_uuid(),
  org_id     uuid references organizations(id) on delete cascade,
  stream_id  uuid references streams(id),
  role       text default 'user',   -- user | assistant | system | agent
  content    jsonb not null,        -- {type: "text", text: "..."} o widget
  metadata   jsonb default '{}',
  created_at timestamptz default now()
);

create index idx_messages_stream on messages(stream_id, created_at);

-- ── RAG / Fuentes ─────────────────────────────────────────────────────────────

create table rag_sources (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid references organizations(id) on delete cascade,
  stream_id        uuid references streams(id),
  name             text not null,
  source_type      text,   -- rule | policy | document | url | text | faq
  content          text not null,
  scope            text default 'stream',   -- global | stream
  always_include   boolean default false,   -- true = va siempre al system prompt
  embedding        vector(1536),            -- pgvector
  metadata         jsonb default '{}',
  propagated_from  uuid,                    -- si fue propagada de otro stream
  created_at       timestamptz default now()
);

create index idx_rag_org_scope on rag_sources(org_id, scope);
create index idx_rag_stream on rag_sources(stream_id);

-- Función para búsqueda semántica
create or replace function search_rag_sources(
  org_id_input uuid,
  query_text   text,
  stream_id_input uuid,
  match_limit  int default 5
)
returns table (name text, content text, source_type text, similarity float)
language plpgsql
as $$
begin
  -- Por ahora búsqueda de texto. Cuando se generen embeddings, usar:
  -- order by embedding <=> query_embedding
  return query
    select s.name, s.content, s.source_type, 1.0::float as similarity
    from rag_sources s
    where s.org_id = org_id_input
      and s.always_include = false
      and (s.scope = 'global' or s.stream_id = stream_id_input)
      and s.content ilike '%' || query_text || '%'
    limit match_limit;
end;
$$;

-- ── Skills / Marketplace ──────────────────────────────────────────────────────

create table skills (
  id            uuid primary key default gen_random_uuid(),
  name          text not null,
  slug          text unique not null,
  description   text,
  category      text,
  skill_type    text default 'prompt',  -- prompt | worker | bundle
  author_org_id uuid references organizations(id),
  is_official   boolean default false,
  is_public     boolean default true,
  price_usd     numeric default 0,
  connectors_required text[] default '{}',
  config_schema jsonb default '{}',
  content       text,                   -- para skills .md
  version       text default '1.0.0',
  downloads     integer default 0,
  rating        numeric default 0,
  created_at    timestamptz default now()
);

-- Skills instaladas en una organización
create table installed_skills (
  id         uuid primary key default gen_random_uuid(),
  org_id     uuid references organizations(id) on delete cascade,
  skill_id   uuid references skills(id),
  stream_id  uuid references streams(id),
  config     jsonb default '{}',
  installed_at timestamptz default now()
);

-- ── Audit Log ─────────────────────────────────────────────────────────────────

create table audit_log (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid references organizations(id) on delete cascade,
  action       text not null,
  actor_type   text,   -- agent | user | system | external
  actor_id     text,
  stream_id    uuid,
  job_id       uuid,
  input_data   jsonb,
  output_data  jsonb,
  status       text,   -- ok | error | pending | skipped
  metadata     jsonb default '{}',
  prev_hash    text,
  hash         text unique,
  created_at   timestamptz default now()
);

create index idx_audit_org on audit_log(org_id, created_at desc);
create index idx_audit_stream on audit_log(stream_id, created_at desc);
create index idx_audit_action on audit_log(org_id, action);

-- ── Dashboard / KPIs ─────────────────────────────────────────────────────────

create table custom_kpis (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid references organizations(id) on delete cascade,
  stream_id    uuid references streams(id),
  name         text not null,
  description  text,
  query_type   text default 'count',  -- count | sum | avg | custom_sql
  config       jsonb default '{}',    -- fuente, filtros, etc.
  is_active    boolean default true,
  created_at   timestamptz default now()
);

-- ── Portal externo ────────────────────────────────────────────────────────────

create table portal_sessions (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references organizations(id) on delete cascade,
  external_user_id uuid references external_users(id),
  token           text unique not null,
  stream_id       uuid references streams(id),
  expires_at      timestamptz,
  created_at      timestamptz default now()
);

-- Tareas asignadas a usuarios externos
create table portal_tasks (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid references organizations(id) on delete cascade,
  stream_id        uuid references streams(id),
  external_user_id uuid references external_users(id),
  title            text not null,
  description      text,
  task_type        text,  -- upload | photo | video | gps | approval | form
  config           jsonb default '{}',
  status           text default 'pending',
  response         jsonb,
  due_at           timestamptz,
  created_at       timestamptz default now(),
  completed_at     timestamptz
);

-- ============================================================
-- Row Level Security (RLS)
-- Asegurar que cada org solo ve sus propios datos
-- ============================================================

alter table organizations      enable row level security;
alter table streams             enable row level security;
alter table agents              enable row level security;
alter table jobs                enable row level security;
alter table messages            enable row level security;
alter table connectors          enable row level security;
alter table rag_sources         enable row level security;
alter table audit_log           enable row level security;
alter table stream_notifications enable row level security;
alter table portal_tasks        enable row level security;

-- Las políticas RLS se configuran según el sistema de auth de Supabase
-- Ver docs/rls_policies.sql para las políticas completas
