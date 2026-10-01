#!/usr/bin/env bash
# Runs the three slowest dashboard queries before and after their index/rewrite, prints ms per variant.
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=tests/reports/explain; mkdir -p "$OUT"
psql() { docker compose exec -T timescale psql -U postgres -d orbit -v ON_ERROR_STOP=1 -qAt "$@"; }
ms() { grep -Eo 'Execution Time: [0-9.]+' | awk '{print $3}'; }

# Seed alerts so the feed query has realistic volume. Rows are tagged {"bench": true} and removed on exit,
# so demo data (open alerts, alert feeds) is never polluted.
cleanup() { docker compose exec -T timescale psql -U postgres -d orbit -q >/dev/null <<'SQL'
DELETE FROM alert WHERE payload @> '{"bench": true}';
VACUUM ANALYZE alert;
SQL
}
trap cleanup EXIT
docker compose exec -T timescale psql -U postgres -d orbit -q -v ON_ERROR_STOP=1 <<'SQL'
INSERT INTO alert(tenant_id, vehicle_id, type, severity, ts, payload, acked_by, acked_ts)
SELECT v.tenant_id, v.id, (ARRAY['LOW_SOC','SOC_DROP','SOH_DROP','STRAND_RISK'])[1 + (g % 4)],
       (ARRAY['WARN','WARN','WARN','CRITICAL'])[1 + (g % 4)], now() - (g || ' seconds')::interval,
       '{"bench": true}',
       -- realistic mix: ~95 % of historical alerts have been acknowledged
       CASE WHEN g % 20 = 0 THEN NULL ELSE 'bench' END, CASE WHEN g % 20 = 0 THEN NULL ELSE now() END
FROM generate_series(1, 400000) g JOIN vehicle v ON v.id = 1 + (g % 100000)
WHERE NOT EXISTS (SELECT 1 FROM alert WHERE payload @> '{"bench": true}');
SQL
psql -c "ANALYZE alert; ANALYZE vehicle; ANALYZE telemetry;"

run() { # name, sql
  psql -c "EXPLAIN (ANALYZE, BUFFERS) $2" > "$OUT/$1.txt"
  printf '%s\t%s\n' "$1" "$(ms < "$OUT/$1.txt")"
}

echo "--- Q1 alert feed page 50 for a tenant: OFFSET vs keyset"
psql -c "DROP INDEX IF EXISTS alert_feed_idx;"
run q1_before "SELECT id, vehicle_id, type, severity, ts FROM alert WHERE tenant_id = 1 ORDER BY ts DESC, id DESC OFFSET 2450 LIMIT 50"
psql -c "CREATE INDEX IF NOT EXISTS alert_feed_idx ON alert (tenant_id, ts DESC, id DESC);"
C=$(psql -c "SELECT ts || '|' || id FROM alert WHERE tenant_id = 1 ORDER BY ts DESC, id DESC OFFSET 2449 LIMIT 1")
TS=${C%|*}; ID=${C#*|}
run q1_after "SELECT id, vehicle_id, type, severity, ts FROM alert WHERE tenant_id = 1 AND (ts, id) < ('$TS'::timestamptz, $ID) ORDER BY ts DESC, id DESC LIMIT 50"

echo "--- Q2 open critical alerts count per tenant: full scan vs partial index"
psql -c "DROP INDEX IF EXISTS alert_open_idx;"
run q2_before "SELECT tenant_id, count(*) FROM alert WHERE acked_ts IS NULL AND severity = 'CRITICAL' GROUP BY tenant_id"
psql -c "CREATE INDEX IF NOT EXISTS alert_open_idx ON alert (tenant_id, severity) WHERE acked_ts IS NULL;"
run q2_after "SELECT tenant_id, count(*) FROM alert WHERE acked_ts IS NULL AND severity = 'CRITICAL' GROUP BY tenant_id"

echo "--- Q3 vehicle list page with model + OEM: N+1 style vs single join with keyset on fleet"
psql -c "DROP INDEX IF EXISTS vehicle_fleet_idx;"
run q3_before "SELECT v.id, v.vin, (SELECT name FROM vehicle_model m WHERE m.id = v.model_id), (SELECT o.code FROM vehicle_model m JOIN oem o ON o.id = m.oem_id WHERE m.id = v.model_id) FROM vehicle v WHERE v.fleet_id = 2 ORDER BY v.id OFFSET 3000 LIMIT 50"
psql -c "CREATE INDEX IF NOT EXISTS vehicle_fleet_idx ON vehicle (fleet_id, id);"
run q3_after "SELECT v.id, v.vin, m.name, o.code FROM vehicle v JOIN vehicle_model m ON m.id = v.model_id JOIN oem o ON o.id = m.oem_id WHERE v.fleet_id = 2 AND v.id > (SELECT id FROM vehicle WHERE fleet_id = 2 ORDER BY id OFFSET 2999 LIMIT 1) ORDER BY v.id LIMIT 50"

echo "--- Q4 latest state per VIN in a fleet from the hypertable: DISTINCT ON vs Redis (documented in ADR-004)"
run q4_distinct_on "SELECT DISTINCT ON (t.vin) t.vin, t.ts, t.soc_pct FROM telemetry t JOIN vehicle v ON v.vin = t.vin WHERE v.fleet_id = 2 AND t.ts > now() - interval '10 minutes' ORDER BY t.vin, t.ts DESC"
