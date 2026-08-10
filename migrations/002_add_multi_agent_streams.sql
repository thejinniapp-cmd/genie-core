-- Migration: Add multi-agent streams support
-- Agregamos columnas para soportar streams multi-agente

ALTER TABLE streams
ADD COLUMN IF NOT EXISTS stream_type VARCHAR DEFAULT 'conversational';
-- stream_type: 'conversational' | 'multi_agent'

ALTER TABLE streams
ADD COLUMN IF NOT EXISTS leader_agent_id UUID REFERENCES agents(id);
-- leader_agent_id: solo populated si stream_type='multi_agent'

ALTER TABLE messages
ADD COLUMN IF NOT EXISTS author VARCHAR DEFAULT 'user';
-- author: 'user' | 'cfo' | 'mba' | 'analytics' | 'talanthub' | 'cmo' | 'cto'
