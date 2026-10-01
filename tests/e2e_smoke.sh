#!/usr/bin/env bash
# End-to-end smoke against the running compose stack: auth, live state, alerts, plan lifecycle, WS, RLS.
set -euo pipefail
API=${API_BASE:-http://localhost:8000}/api/v1
j() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
tok() { curl -sf -X POST "$API/auth/dev-token" -H 'content-type: application/json' -d "{\"tenant_id\":$1,\"role\":\"$2\",\"sub\":\"smoke-$2-$1\"}" | j 'd["access_token"]'; }
T1=$(tok 1 manager); H="Authorization: Bearer $T1"
echo "summary      : $(curl -sf "$API/fleet/summary" -H "$H")"
VIN=$(curl -sf "$API/vehicles?limit=5" -H "$H" | j '[v["vin"] for v in d["items"] if v["state"]][0]')
echo "vehicle      : $VIN"
echo "state        : $(curl -sf "$API/vehicles/$VIN/state" -H "$H" | j '{k:d[k] for k in ("status","soc_pct","speed_kmh","ts")}')"
echo "chargers     : $(curl -sf "$API/vehicles/$VIN/nearest-chargers?limit=3" -H "$H" | j '[(c["name"],c["distance_km"],c["reachable"]) for c in d["items"]]')"
echo "alerts       : $(curl -sf "$API/alerts?limit=200" -H "$H" | j 'sorted({a["type"] for a in d["items"]})')"
DEP=$(python3 -c "from datetime import datetime,timedelta,timezone as z; t=datetime.now(z.utc)+timedelta(days=1); print(t.replace(hour=0,minute=30,second=0,microsecond=0).isoformat())")
PID=$(curl -sf -X POST "$API/plans" -H "$H" -H 'content-type: application/json' -d "{\"vin\":\"$VIN\",\"target_soc\":90,\"depart_ts\":\"$DEP\"}" | j 'd["plan_id"]')
for i in $(seq 1 30); do S=$(curl -sf "$API/plans/$PID" -H "$H" | j 'd["status"]'); [ "$S" != PENDING ] && break; sleep 1; done
echo "plan $PID     : $(curl -sf "$API/plans/$PID" -H "$H" | j '{"status":d["status"],"cost":d["total_cost"],"baseline":d["baseline_cost"],"savings":d["savings"],"slots":len(d["slots"]),"prices":sorted({s["price_per_kwh"] for s in d["slots"]})}')"
T2=$(tok 2 manager)
code=$(curl -s -o /dev/null -w '%{http_code}' "$API/vehicles/$VIN" -H "Authorization: Bearer $T2")
echo "tenant 2 reads tenant 1 vehicle -> HTTP $code (expect 404)"
TF=$(tok 1 finance)
echo "finance view : $(curl -sf "$API/vehicles/$VIN/state" -H "Authorization: Bearer $TF" | j '(d["lat"],d["lon"])')  (2-decimal mask)"
