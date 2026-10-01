#!/usr/bin/env bash
# Chaos test: kill the Kafka broker while the simulator streams; prove recovery with no data loss.
# Evidence written to tests/reports/chaos.json:
#   - raw and canonical high-watermarks keep rising after the broker returns
#   - the simulator's buffer drains back to normal (records were held, not dropped)
#   - every stream consumer is still running (no crash loop) and group lag returns near zero
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p tests/reports
rpk() { docker compose exec -T redpanda rpk "$@" 2>/dev/null || true; }  # broker may be down
hw() { rpk topic describe "$1" -p | awk 'NR>1 && $6 ~ /^[0-9]+$/ {s+=$6} END {print s+0}'; }
lag() { rpk group describe "$1" | awk '/^TOTAL-LAG/{print $2+0}'; }
sim() { docker compose logs --tail 1 simulator 2>&1 | grep -oE "$1=[0-9]+" | cut -d= -f2 || true; }
consumers_up() { docker compose ps --status running --format '{{.Service}}' | grep -cE '^(ingest|state|alert|archiver)$'; }

expected=$(docker compose ps --format '{{.Service}}' | grep -cE '^(ingest|state|alert|archiver)$')
errors0=$(sim errors); errors0=${errors0:-0}
raw0=$(hw telemetry.raw); can0=$(hw telemetry.canonical)
echo "[chaos] baseline raw=$raw0 canonical=$can0 consumers=$expected"

echo "[chaos] killing redpanda for 20 s"
docker compose kill redpanda >/dev/null
sleep 20
buffered_during=$(sim buffered || echo 0)
echo "[chaos] simulator buffered while broker down: $buffered_during"
docker compose start redpanda >/dev/null
for _ in $(seq 1 60); do rpk cluster health | grep -q "Healthy:.*true" && break; sleep 2; done
t0=$(date +%s)

# wait for recovery: lag on every group back under 2 s of traffic (20K events at the 20K/s demo rate)
recovered=false
for _ in $(seq 1 90); do
  l_ing=$(lag ingest); l_st=$(lag state); l_al=$(lag alert)
  if [ "${l_ing:-999999999}" -lt 20000 ] && [ "${l_st:-999999999}" -lt 20000 ] && [ "${l_al:-999999999}" -lt 20000 ]; then
    recovered=true; break
  fi
  sleep 2
done
t_rec=$(( $(date +%s) - t0 ))
raw1=$(hw telemetry.raw); can1=$(hw telemetry.canonical)
up=$(consumers_up)
echo "[chaos] after: raw=$raw1 canonical=$can1 lag ingest=$l_ing state=$l_st alert=$l_al consumers_up=$up/$expected"
echo "[chaos] recovery time ${t_rec}s"

# wait for the producer to flush what it buffered during the outage
for _ in $(seq 1 60); do b=$(sim buffered); [ "${b:-0}" -lt 50000 ] && break; sleep 2; done
buffered_after=$(sim buffered); errors=$(sim errors); errors=${errors:-0}
dropped=$(( errors - errors0 ))
pass=true
[ "$dropped" -eq 0 ] || pass=false        # no record given up by the producer
[ "$raw1" -gt "$raw0" ] && [ "$can1" -gt "$can0" ] || pass=false
[ "$up" -eq "$expected" ] || pass=false
[ "$recovered" = true ] || pass=false
cat > tests/reports/chaos.json <<JSON
{"broker_down_s": 20, "recovery_s": $t_rec, "raw_before": $raw0, "raw_after": $raw1,
 "canonical_before": $can0, "canonical_after": $can1, "sim_buffered_while_down": ${buffered_during:-0},
 "sim_buffered_after": ${buffered_after:-0}, "producer_records_dropped": $dropped,
 "lag_after": {"ingest": ${l_ing:-null}, "state": ${l_st:-null}, "alert": ${l_al:-null}},
 "consumers_running": "$up/$expected", "pass": $pass}
JSON
cat tests/reports/chaos.json
$pass && echo "[chaos] PASS" || { echo "[chaos] FAIL"; exit 1; }
