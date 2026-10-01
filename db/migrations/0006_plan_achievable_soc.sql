-- A plan whose target cannot be reached before departure is still useful (charge as much as possible), but
-- the user must see that it falls short. The planner records the SoC its schedule actually reaches.
ALTER TABLE charging_plan ADD COLUMN IF NOT EXISTS achievable_soc NUMERIC(5,2);
