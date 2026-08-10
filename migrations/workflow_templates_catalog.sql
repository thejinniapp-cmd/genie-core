-- ============================================================
-- Workflow Templates — catálogo vs. copias privadas de uso
-- Ejecutar en Supabase SQL Editor
-- ============================================================

ALTER TABLE workflow_templates
  ADD COLUMN IF NOT EXISTS is_catalog BOOLEAN DEFAULT TRUE;

-- Las plantillas creadas hasta ahora (manual o por lenguaje natural) son base de catálogo.
UPDATE workflow_templates SET is_catalog = TRUE WHERE is_catalog IS NULL;

CREATE INDEX IF NOT EXISTS idx_workflow_templates_catalog ON workflow_templates(org_id, is_catalog, is_active);
