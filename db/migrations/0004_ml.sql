-- ML layer (services/ml): tenant-scoped battery_health with an HNSW index over the degradation embedding.
-- Idempotent: safe to re-run.

-- tenant_id lets the nearest-neighbour query filter by tenant without a join (and lets RLS apply);
-- filled from vehicle by trigger, like charging_plan in 0003.
ALTER TABLE battery_health ADD COLUMN IF NOT EXISTS tenant_id INT REFERENCES tenant(id);
-- 30-day SoH forecast from the SoH model (NULL for OEM-reported rows).
ALTER TABLE battery_health ADD COLUMN IF NOT EXISTS soh_30d_pct NUMERIC(5,2);

CREATE OR REPLACE FUNCTION battery_health_fill_tenant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.tenant_id IS NULL THEN
    SELECT tenant_id INTO NEW.tenant_id FROM vehicle WHERE id = NEW.vehicle_id;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS battery_health_fill_tenant ON battery_health;
CREATE TRIGGER battery_health_fill_tenant BEFORE INSERT ON battery_health
  FOR EACH ROW EXECUTE FUNCTION battery_health_fill_tenant();
UPDATE battery_health b SET tenant_id = v.tenant_id FROM vehicle v WHERE v.id = b.vehicle_id AND b.tenant_id IS NULL;

-- Approximate nearest neighbours (L2) over the unit-norm embeddings. Only the newest row per vehicle keeps an
-- embedding (orbit_ml clears older ones), so the index holds one vector per vehicle.
CREATE INDEX IF NOT EXISTS battery_health_embedding_hnsw ON battery_health
  USING hnsw (embedding vector_l2_ops) WITH (m = 16, ef_construction = 64);

ALTER TABLE battery_health ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON battery_health;
CREATE POLICY tenant_isolation ON battery_health
  USING (tenant_id = current_tenant_id()) WITH CHECK (tenant_id = current_tenant_id());
