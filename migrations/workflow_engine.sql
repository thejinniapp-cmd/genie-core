-- ============================================================
-- Workflow Engine — Jinni
-- Ejecutar en Supabase SQL Editor
-- ============================================================

-- ── Templates ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workflow_templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    trigger_type    TEXT NOT NULL DEFAULT 'manual',  -- manual | webhook | schedule | message
    trigger_config  JSONB DEFAULT '{}',
    has_anchor_date BOOLEAN DEFAULT FALSE,           -- para APQP: fecha de lanzamiento
    is_active       BOOLEAN DEFAULT TRUE,
    created_by      UUID,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workflow_steps (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id         UUID NOT NULL REFERENCES workflow_templates(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    description         TEXT,
    step_order          INTEGER NOT NULL,
    type                TEXT NOT NULL DEFAULT 'human_internal', -- agent | human_internal | human_external | integration | condition | wait
    agent_id            UUID,                        -- si type=agent
    assigned_to         TEXT,                        -- email o role si type=human_internal
    condition_expr      TEXT,                        -- expresión de avance: "output.approved == true"
    advance_condition   TEXT DEFAULT 'all_approved', -- all_approved | any_approved | expression
    deadline_offset_days INTEGER,                   -- días relativos al anchor_date
    alert_before_days   INTEGER DEFAULT 1,
    config              JSONB DEFAULT '{}',
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workflow_tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    step_id         UUID NOT NULL REFERENCES workflow_steps(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    input_type      TEXT DEFAULT 'approval',  -- approval | file | text | number | date | signature
    assigned_to_role TEXT DEFAULT 'reviewer', -- reviewer | applicant | agent
    reviewer_emails TEXT[] DEFAULT '{}',
    depends_on      UUID[] DEFAULT '{}',      -- IDs de otras tasks del mismo step
    template_url    TEXT,                     -- plantilla descargable (ej: FMEA template)
    required_fields JSONB DEFAULT '[]',       -- validación de contenido del documento
    versioned       BOOLEAN DEFAULT FALSE,
    task_config     JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ── Runs (instancias activas) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS workflow_runs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id UUID NOT NULL REFERENCES workflow_templates(id),
    org_id      UUID NOT NULL,
    stream_id   UUID,
    name        TEXT,                          -- nombre descriptivo de esta instancia
    status      TEXT DEFAULT 'pending',        -- pending | running | waiting | completed | failed | cancelled
    input_data  JSONB DEFAULT '{}',            -- datos de entrada que dispararon el run
    anchor_date DATE,                          -- fecha de referencia (ej: launch date para APQP)
    started_at  TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_by  UUID,
    metadata    JSONB DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS workflow_run_steps (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       UUID NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    step_id      UUID NOT NULL REFERENCES workflow_steps(id),
    status       TEXT DEFAULT 'pending',       -- pending | in_progress | completed | skipped | failed
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    output_data  JSONB DEFAULT '{}',           -- output de este paso (input para el siguiente)
    deadline_at  TIMESTAMPTZ,                  -- calculado desde anchor_date + offset
    alerted_at   TIMESTAMPTZ                   -- cuando se mandó alerta de deadline
);

CREATE TABLE IF NOT EXISTS workflow_run_tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_step_id     UUID NOT NULL REFERENCES workflow_run_steps(id) ON DELETE CASCADE,
    task_id         UUID NOT NULL REFERENCES workflow_tasks(id),
    assigned_to     TEXT,                      -- email o user_id
    type            TEXT,                      -- hereda del step: human_external | human_internal | agent
    status          TEXT DEFAULT 'pending',    -- pending | in_progress | approved | rejected | completed
    response_data   JSONB DEFAULT '{}',
    approved_at     TIMESTAMPTZ,
    rejected_at     TIMESTAMPTZ,
    rejection_reason TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reviewer_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_task_id     UUID NOT NULL REFERENCES workflow_run_tasks(id) ON DELETE CASCADE,
    token           TEXT NOT NULL UNIQUE DEFAULT encode(gen_random_bytes(32), 'hex'),
    reviewer_email  TEXT NOT NULL,
    expires_at      TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '30 days'),
    used_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS run_task_responses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_task_id     UUID NOT NULL REFERENCES workflow_run_tasks(id) ON DELETE CASCADE,
    response_text   TEXT,
    response_number NUMERIC,
    response_date   DATE,
    response_data   JSONB DEFAULT '{}',
    file_url        TEXT,
    file_name       TEXT,
    file_version    INTEGER DEFAULT 1,
    submitted_by    TEXT NOT NULL,
    submitted_at    TIMESTAMPTZ DEFAULT NOW()
);


-- ── Índices ───────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_workflow_templates_org ON workflow_templates(org_id);
CREATE INDEX IF NOT EXISTS idx_workflow_steps_template ON workflow_steps(template_id, step_order);
CREATE INDEX IF NOT EXISTS idx_workflow_tasks_step ON workflow_tasks(step_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_org ON workflow_runs(org_id, status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_stream ON workflow_runs(stream_id);
CREATE INDEX IF NOT EXISTS idx_workflow_run_steps_run ON workflow_run_steps(run_id, status);
CREATE INDEX IF NOT EXISTS idx_workflow_run_tasks_step ON workflow_run_tasks(run_step_id, status);
CREATE INDEX IF NOT EXISTS idx_reviewer_links_token ON reviewer_links(token);
CREATE INDEX IF NOT EXISTS idx_reviewer_links_email ON reviewer_links(reviewer_email);


-- ── RLS ──────────────────────────────────────────────────────
ALTER TABLE workflow_templates  ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_steps      ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_tasks      ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_runs       ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_run_steps  ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_run_tasks  ENABLE ROW LEVEL SECURITY;
ALTER TABLE reviewer_links      ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_task_responses  ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS (el backend usa service key)
-- Las políticas de acceso por usuario se agregan cuando se implemente auth en frontend


-- ── Webhook events log (para auditoría inmutable) ─────────────
CREATE TABLE IF NOT EXISTS workflow_events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    run_id      UUID REFERENCES workflow_runs(id),
    event_type  TEXT NOT NULL,  -- run.started | step.completed | task.approved | task.rejected | run.completed
    actor       TEXT,           -- email o "agent:{agent_id}" o "system"
    payload     JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workflow_events_run ON workflow_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_events_org ON workflow_events(org_id, created_at);
