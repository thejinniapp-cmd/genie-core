-- Migration: Create agents table for multi-agent system
-- Define sistema de agentes con roles y delegaciones

-- Crear tabla agents
CREATE TABLE IF NOT EXISTS agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name VARCHAR NOT NULL,
  display_name VARCHAR NOT NULL,
  type VARCHAR DEFAULT 'system', -- 'system' | 'custom'
  system_prompt TEXT NOT NULL,
  model_id VARCHAR DEFAULT 'claude-3.5-sonnet',
  temperature FLOAT DEFAULT 0.3,
  max_tokens INTEGER DEFAULT 2048,
  can_delegate_to TEXT[] DEFAULT '{}', -- JSON array of agent names: ['mba', 'analytics', ...]
  tools TEXT[] DEFAULT '{}', -- ['sheets_*', 'docs_*', ...]
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(org_id, name)
);

-- Crear índices
CREATE INDEX IF NOT EXISTS idx_agents_org_id ON agents(org_id);
CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name);
CREATE INDEX IF NOT EXISTS idx_agents_is_active ON agents(is_active);

-- RLS
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Lectura agentes por org" ON agents
  FOR SELECT USING (org_id = current_setting('app.current_org_id', true)::uuid OR current_setting('app.is_service_role', true)::text = 'true');

CREATE POLICY "Insert agentes por org" ON agents
  FOR INSERT WITH CHECK (org_id = current_setting('app.current_org_id', true)::uuid OR current_setting('app.is_service_role', true)::text = 'true');

-- Arreglar relación en streams si la tabla agents no existía
ALTER TABLE streams DROP CONSTRAINT IF EXISTS streams_leader_agent_id_fkey;
ALTER TABLE streams ADD CONSTRAINT streams_leader_agent_id_fkey
  FOREIGN KEY (leader_agent_id) REFERENCES agents(id) ON DELETE SET NULL;

-- Nota: Los agentes se poblan per-org mediante un endpoint API
-- El SQL de aquí solo crea la tabla. Ver: POST /api/org/{org_id}/agents/init
