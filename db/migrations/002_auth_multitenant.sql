-- ============================================================
-- 002_auth_multitenant.sql
-- Multi-tenant: user_organizations + trigger de creación de org
-- ============================================================

-- Tabla de relación usuario <-> organización
CREATE TABLE IF NOT EXISTS user_organizations (
  id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id     UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  org_id      UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  role        TEXT NOT NULL DEFAULT 'owner' CHECK (role IN ('owner', 'consultant', 'member')),
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, org_id)
);

-- Columna metadata en organizations (si no existe)
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

-- Columna created_by en organizations
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES auth.users(id);

-- RLS: users can only see their own orgs
ALTER TABLE user_organizations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users see their own orgs"
  ON user_organizations FOR SELECT
  USING (user_id = auth.uid());

CREATE POLICY "Users insert their own orgs"
  ON user_organizations FOR INSERT
  WITH CHECK (user_id = auth.uid());

-- Trigger: crear org automáticamente cuando se registra un usuario
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
  new_org_id UUID;
  display_name TEXT;
  org_slug TEXT;
BEGIN
  display_name := COALESCE(
    NEW.raw_user_meta_data->>'full_name',
    split_part(NEW.email, '@', 1),
    'Mi Empresa'
  );

  org_slug := lower(regexp_replace(display_name, '[^a-zA-Z0-9]', '-', 'g'))
              || '-' || substring(NEW.id::text, 1, 8);

  INSERT INTO organizations (name, slug, plan, status, created_by)
  VALUES (display_name, org_slug, 'starter', 'onboarding', NEW.id)
  RETURNING id INTO new_org_id;

  INSERT INTO user_organizations (user_id, org_id, role)
  VALUES (NEW.id, new_org_id, 'owner');

  RETURN NEW;
END;
$$;

-- Drop trigger if exists, then recreate
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION handle_new_user();

-- RLS en organizations: users can see/update orgs they belong to
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their orgs"
  ON organizations FOR SELECT
  USING (
    id IN (
      SELECT org_id FROM user_organizations WHERE user_id = auth.uid()
    )
  );

CREATE POLICY "Owners can update their orgs"
  ON organizations FOR UPDATE
  USING (
    id IN (
      SELECT org_id FROM user_organizations
      WHERE user_id = auth.uid() AND role IN ('owner', 'consultant')
    )
  );

-- Service role bypasses RLS (backend usa service key)
