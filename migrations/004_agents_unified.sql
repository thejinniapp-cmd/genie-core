-- Migration 004: Unificar esquema de agents
-- La migración 003 era CREATE TABLE IF NOT EXISTS y resultó no-op porque
-- 001 ya creó la tabla con otro shape. Esta migración es idempotente y
-- lleva la tabla viva (shape 001) al shape unificado que usa el código:
-- multi-agente (display_name, type, can_delegate_to) + 001 (agent_type,
-- stream_id, config, autonomy_level...).

ALTER TABLE agents ADD COLUMN IF NOT EXISTS display_name text;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS type text DEFAULT 'custom';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS can_delegate_to text[] DEFAULT '{}';

UPDATE agents SET display_name = name WHERE display_name IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS agents_org_name_unique ON agents(org_id, name);
CREATE INDEX IF NOT EXISTS idx_agents_org_id ON agents(org_id);
CREATE INDEX IF NOT EXISTS idx_agents_is_active ON agents(is_active);

-- Upsert de skills instaladas (api/routes/skills.py usa on_conflict org+skill+stream)
CREATE UNIQUE INDEX IF NOT EXISTS installed_skills_org_skill_stream_unique
  ON installed_skills(org_id, skill_id, stream_id);
