# ORBIT ML and vector layer (solution document §11)

Code: `services/ml` (package `orbit_ml`). Run it with `python -m orbit_ml train | publish | train-and-publish | evaluate`,
or in compose with `docker compose --profile ml run --rm ml`. Metrics from the latest training run are in
[`docs/ml-metrics.json`](ml-metrics.json).

## 1. Purpose: the decisions each model supports

| Model | Decision it supports | Why fixed rules are not enough |
|---|---|---|
| **Remaining range** (km) | Planner and dispatcher: can this vehicle finish its next trip, or should it charge first? Alert `STRAND_RISK` and `/nearest-chargers` reachability use the same range. | The rule `nominal_range × SoC` assumes every vehicle drives at the rated consumption. Real consumption depends on the duty cycle. In the simulator, energy per km grows by up to 1.3× with speed, so a fast-route vehicle's range is overstated and a city vehicle's is understated. The rule's error (12 km MAE, 9.8 % MAPE) is about 4× the model's. |
| **SoH in 30 days** (%) | Fleet/maintenance manager: which batteries to inspect, re-assign to short routes or claim on warranty *before* they cross 80 %. The `SOH_DROP` alert only fires after SoH is already below 80. | A linear "x % per km" rule gives every vehicle the fleet-average degradation rate. It cannot see the 2 % fast-degrade cohort, which loses SoH about 20× faster per kWh. The rule misses 40 % of the vehicles that will be below 80 % within 30 days. The model misses 5 %. |
| **Degradation embedding** (pgvector) | "Find vehicles that look like this degrading one": an early-warning cohort for inspection and a check on whether a problem is limited to one model, depot or duty cycle. | A rule can filter on single thresholds, but it cannot rank 12,500 vehicles by overall similarity across 32 usage and health signals. |

## 2. Data and features

Two sources. Each run records which one it used in `metrics.json → data_source`.

**Synthetic (always used for SoH, and for range until enough live labels exist).** `orbit_ml.data.synthetic_fleet()`
mirrors `simulator/internal/fleet/vehicle.go` and `road.go`:

- the same five battery models (capacity and nominal range from `orbit_common.seed.MODELS`);
- SoC drain: kWh/km = capacity / range × (0.8 + 0.5·v/90), with distance weighting over the speed trace;
- SoH loss per kWh charged: 0.0004 %/kWh, or 0.008 %/kWh for the 2 % fast-degrade cohort. Initial SoH is 92–100 (88–94 for the fast cohort) and the initial odometer is 0–150,000 km, as in `Init()`.

The synthetic data differs from the simulator in four places, and each is stated here:

- each vehicle gets its own duty cycle (mean cruise speed around 47 km/h, sd 9; the simulator draws every trip from one distribution);
- the measured efficiency (σ 3 %) and the SoH slope (σ 20 %) carry noise;
- 30 % of snapshots have no driving and 50 % have no charging in the window, so those features are NaN, exactly as they are for a parked live vehicle;
- the next 30 days' kWh varies by ±12 %.

Default size: 6,000 vehicles × 3 snapshots = 18,000 rows, seed 7.

**Live (inference always; range training when ≥ 300 labelled vehicles).** `load_live()` builds one row per
battery vehicle for the last N hours (default 6). It reads raw `telemetry` with `lag()` per VIN to get:

- speed profile: mean and p90 speed while moving, share of time moving;
- driving efficiency: SoC drop per km over intervals with no charging and a rising odometer (requires ≥ 2 km and ≥ 0.5 % SoC);
- energy: kWh charged (Σ kW·dt) and the DC share;
- ambient temperature: mean, max and share of time above 35 °C;
- SoH: last reading, and slope per 100 kWh charged (requires ≥ 5 kWh);
- odometer, and cycles = odometer / nominal range.

From `telemetry_5m` it takes a 10-bin SoC histogram. Model capacity and nominal range come from `vehicle_model`.
`load_live_range_labels()` makes supervised range rows: features come from the first half of the window, and the
label is the km per SoC % actually driven in the second half. **Live SoH labels do not exist yet** (they need 30 days
of history), so the SoH model is trained only on synthetic data.

Feature sets: range uses `battery_kwh, nominal_range_km, mean_speed_kmh, p90_speed_kmh, moving_frac, ambient_c_mean,
soh_pct, obs_kwh_per_km`. SoH uses `soh_pct, soh_slope_per_100kwh, km_per_day, dc_kwh_share, ambient_c_mean, cycles,
odo_km, battery_kwh, nominal_range_km`.

## 3. Leakage checks

- **Split by vehicle, never by time.** `is_test_vehicle(vin)` = sha256(salt:vin) mod 100 < 20. The split is stable across runs and across datasets, so a live VIN that is in test today is in test tomorrow. The synthetic VINs (`SYN…`) cannot collide with real ones.
- `assert_no_leakage()` runs inside every split and raises if a VIN has rows on both sides. Tests: `test_split_is_by_vehicle_and_stable`, `test_leakage_check_detects_overlap`.
- Live range labels come only from the second half of the window, and features only from the first half (`test_live_range_labels_half_window` checks that the boundaries meet and do not overlap).
- Targets are never features. The SoH model sees today's SoH and slope, not SoH in 30 days. The range model sees past efficiency, not the future speed trace.

## 4. Model choice

| Target | Model | Why |
|---|---|---|
| km per SoC % (range = SoC × it) | `HistGradientBoostingRegressor` (300 iterations, lr 0.08) | Handles NaN natively (vehicles that did not drive), captures the non-linear speed effect, trains in seconds. Predicting km per % instead of km means one number per vehicle can be published to Redis and multiplied by live SoC. |
| SoH in 30 days | `GradientBoostingRegressor` (300 trees, depth 3) behind a median imputer with missing-value indicators | Small tabular problem. The indicator tells the model when the slope is unknown (no charge seen), so it falls back on the SoH level and usage. |
| Baselines | range: `nominal × SoC / 100`; SoH: current SoH − fleet-median loss per km × expected km in 30 days (linear decline by odometer). Also reported: a linear regression on cycles and odometer only. | These are the rules the system would use without ML. |

## 5. Metrics vs baseline (held-out vehicles)

Real run of `train-and-publish` on 2026-10-01 at 00:59 UTC. The training set was synthetic: the live window
was around 06:00 IST, at the start of shift, so 0 vehicles had enough driving in both half-windows to give a label.
The test set is 1,201 held-out vehicles (3,603 rows).

| Model | Metric | Model | Baseline | Cycles-only reference |
|---|---|---|---|---|
| Range (km) | MAE | **2.93 km** | 11.94 km | – |
| Range (km) | MAPE | **2.30 %** | 9.81 % | – |
| SoH in 30 d | MAE | **0.11 pts** | 0.27 pts | 2.66 pts |
| SoH in 30 d | Recall, SoH < 80 | **0.95** | 0.60 | 0.00 |
| SoH in 30 d | Precision, SoH < 80 | 0.94 | 1.00 | 0.00 |

(63 positive rows in test.) The baseline's precision is 1.00 because it only flags vehicles that are already near
80 %. The model gives up 6 points of precision to catch 35 points more of the vehicles that are about to cross.
The tests assert these properties on a fixed seed: the model's MAE is below 0.6× the baseline's, and its recall is
above both baselines.

## 6. Vector design (pgvector)

- **Vector:** 32 interpretable dimensions, with no learned projection, so every axis can be explained. They are 10 SoC-histogram bins, 5 charge-pattern features, 3 temperature-exposure features, 4 duty-cycle features, 4 SoH state/trend features, 2 efficiency features, 2 battery-size features, and 2 model outputs (predicted 30-day SoH loss, and the deficit against the fleet degradation line).
- **Scaling:** each dimension is standardised over the run's population (NaN → mean, clipped at ±5 sd) and weighted. SoH level, slope, predicted loss and deficit count ×2; histogram bins count ×0.5. The vector is then L2-normalised, so L2 distance ranks vehicles by profile similarity. In tests, a fast-degrader's 5 nearest neighbours are mostly fast-degraders (`test_degrading_vehicles_are_nearest_to_each_other`).
- **Storage:** `battery_health` rows with `method = 'model_v1'`, `soh_pct` (latest reading), `cycles`, `embedding`, and the new `soh_30d_pct`. History rows are kept for `trend_30d`. Only the newest row per vehicle keeps its embedding, so the index holds one vector per vehicle and every vector comes from the same standardisation.
- **Index:** `db/migrations/0004_ml.sql` adds:
  - `battery_health_embedding_hnsw` = `hnsw (embedding vector_l2_ops)` with m 16 and ef_construction 64;
  - a `tenant_id` column (filled by trigger from `vehicle`, then backfilled);
  - RLS `tenant_isolation` on `battery_health`.
- **Query (`/reports/battery-health`):**
  - It pages vehicles by VIN keyset and takes the latest row per vehicle with a LATERAL query on `battery_health_vehicle_idx`.
  - The `similar` list is a per-row KNN subquery on `battery_health` alone: `WHERE b3.tenant_id = $1 AND embedding IS NOT NULL AND vehicle_id <> self ORDER BY embedding <-> l.embedding LIMIT 5`. It has no join inside, so the planner can use the HNSW index ordering.
  - The repository runs `SET LOCAL hnsw.iterative_scan = strict_order` (pgvector ≥ 0.8; the stack runs 0.8.6). The tenant filter then cannot starve the result: the scan keeps walking the graph until it has 5 neighbours from the same tenant.
  - Tenant scoping is applied three times: in the KNN filter, in the VIN join, and by RLS.

## 7. Guardrails

- The API never loads a model. It reads `range:{vin}` from Redis, and if the key is missing, malformed, non-finite or non-positive, it falls back to `baseline_v0`. Confidence = 1 − MAE / range, capped at 0.95, so short ranges get honestly low confidence.
- Published coefficients are clipped to [0.6, 1.3] × nominal km per % (the physical envelope of the simulator's speed factor). SoH forecasts are clipped to [0, current SoH], since SoH never recovers.
- Keys expire after 1 day, so a stopped job degrades to the baseline instead of serving stale coefficients.
- `publish` refuses to write if migration 0004 is missing. All `battery_health` writes happen in one transaction (clear old embeddings, then insert).
- Artifacts are joblib files that only this job produces. They are never loaded from user input. `load()` type-checks them.
- The job reads telemetry as the admin role but writes only `battery_health` and `range:*`.

## 8. Cost and latency (laptop, M-series)

| Step | Time |
|---|---|
| Generate 18k synthetic rows | 0.3 s |
| Fit both models and baselines | 4.5 s |
| Live feature query, 6 h window, about 97k vehicles | about 80 s (window sort over raw telemetry) |
| Predict and embed 100k vehicles | 0.3 s + 0.06 s |
| API range-forecast, extra cost | one Redis GET (sub-millisecond) |
| API battery-health | one HNSW probe per returned row (page ≤ 50) |
| Model artifact | 0.75 MB |

There is no GPU and no LLM. The job is a batch run (cron or compose profile `ml`), daily, which matches the 1-day TTL.

## 9. Limitations

- **Labels are synthetic.** They follow the simulator's physics, which has no temperature effect on consumption and no SoH effect on usable capacity. The model therefore learns that temperature is irrelevant, which is not true for real packs. The headline metrics measure how well the model recovers the simulator, not real-world accuracy.
- **No live SoH labels yet.** Live range labels need vehicles that drove ≥ 5 km in both half-windows. At night or at shift start there are none, so training falls back to synthetic data (recorded in `data_source`).
- **Live SoH slope needs a charge of ≥ 5 kWh inside the window.** Until a vehicle charges, the model relies on SoH level and usage, and fast-degraders that have not charged recently are harder to flag.
- **The embeddings are refitted every run** (population standardisation), so vectors are only comparable within one run. Older rows' embeddings are cleared on purpose.
- **Known data issue:** `telemetry_5m.kwh_charged = sum(charging_kw)/12` overstates kWh by the number of samples per bucket (it should be `avg(kW)·5/60`). orbit_ml does not use that column; it computes Σ kW·dt from raw telemetry.
