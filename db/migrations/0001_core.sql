-- ORBIT relational core (3NF). Applied by `make migrate` (db/migrate.py) in filename order.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenant (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oem (
  id              SERIAL PRIMARY KEY,
  code            TEXT NOT NULL UNIQUE,           -- OEM_A, OEM_B
  name            TEXT NOT NULL,
  payload_format  TEXT NOT NULL                   -- adapter key
);

CREATE TABLE IF NOT EXISTS vehicle_model (
  id                    SERIAL PRIMARY KEY,
  oem_id                INT NOT NULL REFERENCES oem(id),
  name                  TEXT NOT NULL,
  powertrain            TEXT NOT NULL CHECK (powertrain IN ('ICE','HEV','PHEV','BEV')),
  battery_capacity_kwh  NUMERIC(6,1),
  nominal_range_km      NUMERIC(6,1),
  max_ac_kw             NUMERIC(5,1),
  max_dc_kw             NUMERIC(5,1),
  UNIQUE (oem_id, name)
);

CREATE TABLE IF NOT EXISTS depot (
  id                 SERIAL PRIMARY KEY,
  tenant_id          INT NOT NULL REFERENCES tenant(id),
  name               TEXT NOT NULL,
  lat                DOUBLE PRECISION NOT NULL,
  lon                DOUBLE PRECISION NOT NULL,
  grid_capacity_kw   NUMERIC(8,1) NOT NULL,
  UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS fleet (
  id          SERIAL PRIMARY KEY,
  tenant_id   INT NOT NULL REFERENCES tenant(id),
  depot_id    INT REFERENCES depot(id),
  name        TEXT NOT NULL,
  UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS vehicle (
  id          BIGSERIAL PRIMARY KEY,
  vin         CHAR(17) NOT NULL UNIQUE,
  tenant_id   INT NOT NULL REFERENCES tenant(id),
  fleet_id    INT NOT NULL REFERENCES fleet(id),
  model_id    INT NOT NULL REFERENCES vehicle_model(id),
  plate       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS vehicle_fleet_idx ON vehicle (fleet_id, id);
CREATE INDEX IF NOT EXISTS vehicle_tenant_idx ON vehicle (tenant_id, id);

CREATE TABLE IF NOT EXISTS driver (
  id            BIGSERIAL PRIMARY KEY,
  tenant_id     INT NOT NULL REFERENCES tenant(id),
  name_masked   TEXT NOT NULL,                 -- "R*** S***", real name never stored
  licence_hash  TEXT NOT NULL,                 -- sha256, for erasure lookups
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  erased_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS driver_assignment (
  id          BIGSERIAL PRIMARY KEY,
  driver_id   BIGINT NOT NULL REFERENCES driver(id) ON DELETE CASCADE,
  vehicle_id  BIGINT NOT NULL REFERENCES vehicle(id),
  start_ts    TIMESTAMPTZ NOT NULL,
  end_ts      TIMESTAMPTZ,
  CHECK (end_ts IS NULL OR end_ts > start_ts)
);
CREATE INDEX IF NOT EXISTS driver_assignment_vehicle_idx ON driver_assignment (vehicle_id, start_ts DESC);

CREATE TABLE IF NOT EXISTS charger (
  id          SERIAL PRIMARY KEY,
  tenant_id   INT REFERENCES tenant(id),        -- NULL = public charger
  depot_id    INT REFERENCES depot(id),
  name        TEXT NOT NULL,
  lat         DOUBLE PRECISION NOT NULL,
  lon         DOUBLE PRECISION NOT NULL,
  max_kw      NUMERIC(6,1) NOT NULL,
  connector   TEXT NOT NULL CHECK (connector IN ('CCS2','CHAdeMO','Type2','GBT')),
  is_public   BOOLEAN NOT NULL DEFAULT false,
  price_per_kwh NUMERIC(8,4)                     -- NULL = use depot tariff
);
CREATE INDEX IF NOT EXISTS charger_geo_idx ON charger (lat, lon);

CREATE TABLE IF NOT EXISTS tariff (
  id          SERIAL PRIMARY KEY,
  tenant_id   INT NOT NULL REFERENCES tenant(id),
  name        TEXT NOT NULL,
  timezone    TEXT NOT NULL DEFAULT 'Asia/Kolkata',
  UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS tariff_slot (
  id             SERIAL PRIMARY KEY,
  tariff_id      INT NOT NULL REFERENCES tariff(id) ON DELETE CASCADE,
  dow            SMALLINT NOT NULL CHECK (dow BETWEEN 0 AND 6),   -- 0 = Monday
  start_min      SMALLINT NOT NULL CHECK (start_min BETWEEN 0 AND 1439),
  end_min        SMALLINT NOT NULL CHECK (end_min BETWEEN 1 AND 1440),
  price_per_kwh  NUMERIC(8,4) NOT NULL,
  CHECK (end_min > start_min)
);
CREATE INDEX IF NOT EXISTS tariff_slot_lookup_idx ON tariff_slot (tariff_id, dow, start_min);

ALTER TABLE depot ADD COLUMN IF NOT EXISTS tariff_id INT REFERENCES tariff(id);

CREATE TABLE IF NOT EXISTS charging_plan (
  id            BIGSERIAL PRIMARY KEY,
  vehicle_id    BIGINT NOT NULL REFERENCES vehicle(id),
  created_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
  target_soc    NUMERIC(5,2) NOT NULL,
  depart_ts     TIMESTAMPTZ NOT NULL,
  total_cost    NUMERIC(10,2),
  total_kwh     NUMERIC(10,2),
  baseline_cost NUMERIC(10,2),                  -- "charge on arrival" cost, for savings
  status        TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','READY','ACTIVE','DONE','CANCELLED','FAILED')),
  requested_by  TEXT NOT NULL                   -- user id or 'agent:<id>'
);
CREATE INDEX IF NOT EXISTS charging_plan_vehicle_idx ON charging_plan (vehicle_id, created_ts DESC);

CREATE TABLE IF NOT EXISTS charging_slot (
  id          BIGSERIAL PRIMARY KEY,
  plan_id     BIGINT NOT NULL REFERENCES charging_plan(id) ON DELETE CASCADE,
  charger_id  INT NOT NULL REFERENCES charger(id),
  start_ts    TIMESTAMPTZ NOT NULL,
  end_ts      TIMESTAMPTZ NOT NULL,
  kw          NUMERIC(6,1) NOT NULL,
  kwh         NUMERIC(8,2) NOT NULL,
  cost        NUMERIC(10,2) NOT NULL,
  CHECK (end_ts > start_ts)
);
CREATE INDEX IF NOT EXISTS charging_slot_plan_idx ON charging_slot (plan_id, start_ts);
CREATE INDEX IF NOT EXISTS charging_slot_charger_time_idx ON charging_slot (charger_id, start_ts);

CREATE TABLE IF NOT EXISTS charging_session (
  id            BIGSERIAL PRIMARY KEY,
  vehicle_id    BIGINT NOT NULL REFERENCES vehicle(id),
  charger_id    INT REFERENCES charger(id),
  start_ts      TIMESTAMPTZ NOT NULL,
  end_ts        TIMESTAMPTZ,
  kwh           NUMERIC(8,2),
  cost          NUMERIC(10,2),
  plan_slot_id  BIGINT REFERENCES charging_slot(id)
);
CREATE INDEX IF NOT EXISTS charging_session_vehicle_idx ON charging_session (vehicle_id, start_ts DESC);

CREATE TABLE IF NOT EXISTS alert (
  id          BIGSERIAL PRIMARY KEY,
  tenant_id   INT NOT NULL REFERENCES tenant(id),
  vehicle_id  BIGINT NOT NULL REFERENCES vehicle(id),
  type        TEXT NOT NULL,                    -- STRAND_RISK, LOW_SOC, SOH_DROP, BATTERY_DTC, ...
  severity    TEXT NOT NULL CHECK (severity IN ('INFO','WARN','CRITICAL')),
  ts          TIMESTAMPTZ NOT NULL,
  payload     JSONB NOT NULL DEFAULT '{}'::jsonb,   -- deliberate denormalisation (ADR-4)
  acked_by    TEXT,
  acked_ts    TIMESTAMPTZ
);
-- keyset pagination: (tenant_id, ts DESC, id DESC)
CREATE INDEX IF NOT EXISTS alert_feed_idx ON alert (tenant_id, ts DESC, id DESC);
CREATE INDEX IF NOT EXISTS alert_open_idx ON alert (tenant_id, severity) WHERE acked_ts IS NULL;

CREATE TABLE IF NOT EXISTS subscription (
  id          SERIAL PRIMARY KEY,
  tenant_id   INT NOT NULL REFERENCES tenant(id),
  plan        TEXT NOT NULL,
  starts      DATE NOT NULL,
  ends        DATE,
  status      TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS user_account (
  id           BIGSERIAL PRIMARY KEY,
  tenant_id    INT NOT NULL REFERENCES tenant(id),
  idp_subject  TEXT NOT NULL UNIQUE,
  email_masked TEXT,
  role         TEXT NOT NULL CHECK (role IN ('admin','manager','finance','auditor')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
  id          BIGSERIAL PRIMARY KEY,
  tenant_id   INT,
  actor_type  TEXT NOT NULL CHECK (actor_type IN ('user','agent','system')),
  actor_id    TEXT NOT NULL,
  action      TEXT NOT NULL,
  resource    TEXT NOT NULL,
  ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
  details     JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS audit_log_tenant_ts_idx ON audit_log (tenant_id, ts DESC);

CREATE TABLE IF NOT EXISTS battery_health (
  id           BIGSERIAL PRIMARY KEY,
  vehicle_id   BIGINT NOT NULL REFERENCES vehicle(id),
  computed_ts  TIMESTAMPTZ NOT NULL DEFAULT now(),
  soh_pct      NUMERIC(5,2) NOT NULL,
  cycles       INT,
  method       TEXT NOT NULL,                  -- 'oem_reported' | 'model_v1'
  embedding    vector(32)                      -- degradation-profile embedding (pgvector)
);
CREATE INDEX IF NOT EXISTS battery_health_vehicle_idx ON battery_health (vehicle_id, computed_ts DESC);

CREATE TABLE IF NOT EXISTS agent_action (
  id           BIGSERIAL PRIMARY KEY,
  tenant_id    INT NOT NULL REFERENCES tenant(id),
  proposed_by  TEXT NOT NULL,                   -- agent run id
  action       TEXT NOT NULL,                   -- REPLAN, ACK_ALERT
  params       JSONB NOT NULL,
  rationale    TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','REJECTED','EXECUTED','FAILED')),
  decided_by   TEXT,
  decided_ts   TIMESTAMPTZ,
  created_ts   TIMESTAMPTZ NOT NULL DEFAULT now()
);
