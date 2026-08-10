-- 010_chief_alert_state.sql
-- Estado y configuración de alertas del Chief of Staff.

CREATE TABLE IF NOT EXISTS public.chief_alert_settings (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null references public.organizations(id) on delete cascade,
  metric_key  text not null,
  threshold   numeric default 0,
  enabled     boolean default true,
  is_lower_bound boolean default false,  -- true: alerta si valor < threshold; false: si valor > threshold
  channels    jsonb default '[]',         -- [{"type":"slack","channel":"#alerts"}, ...]
  created_at  timestamptz default now(),
  updated_at  timestamptz default now(),
  unique(org_id, metric_key)
);

CREATE TABLE IF NOT EXISTS public.chief_alert_state (
  org_id        uuid not null references public.organizations(id) on delete cascade,
  metric_key    text not null,
  last_value    numeric default 0,
  last_alert_at timestamptz,
  primary key (org_id, metric_key)
);

-- RLS básico: solo el service role accede; mantener consistente con el resto de tablas.
ALTER TABLE public.chief_alert_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chief_alert_state ENABLE ROW LEVEL SECURITY;
