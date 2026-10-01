-- Row-level security: tenant isolation enforced in the database, not only in code.
-- API sets `SET LOCAL app.tenant_id = <id>` per transaction; the orbit_app role is subject to RLS.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orbit_app') THEN
    CREATE ROLE orbit_app LOGIN PASSWORD 'orbit_app';
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO orbit_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO orbit_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO orbit_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO orbit_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO orbit_app;

-- charging_plan carries tenant_id (filled by trigger from vehicle) so RLS can be applied without a join.
ALTER TABLE charging_plan ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenant(id);
CREATE OR REPLACE FUNCTION charging_plan_fill_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.tenant_id IS NULL THEN
    SELECT tenant_id INTO NEW.tenant_id FROM vehicle WHERE id = NEW.vehicle_id;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS charging_plan_fill_tenant ON charging_plan;
CREATE TRIGGER charging_plan_fill_tenant BEFORE INSERT ON charging_plan FOR EACH ROW EXECUTE FUNCTION charging_plan_fill_tenant();
UPDATE charging_plan p SET tenant_id = v.tenant_id FROM vehicle v WHERE v.id = p.vehicle_id AND p.tenant_id IS NULL;
CREATE INDEX IF NOT EXISTS charging_plan_tenant_idx ON charging_plan (tenant_id, created_ts DESC, id DESC);

CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS INT LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::INT $$;

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['vehicle','fleet','depot','alert','charging_plan','audit_log','agent_action','user_account','driver','tariff']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id())', t);
  END LOOP;
END $$;
