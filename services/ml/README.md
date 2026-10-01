# orbit-ml

Batch ML job for ORBIT. It trains the remaining-range model (HistGradientBoosting) and the 30-day SoH model
(GradientBoosting), and evaluates each against its rule baseline on held-out vehicles. It then publishes:

- `range:{vin}` coefficients to Redis, which the API uses for `/vehicles/{vin}/range-forecast` (method `hgb_v1`);
- `battery_health` rows with a 32-dim degradation embedding in pgvector, which `/reports/battery-health` uses for `similar`.

```bash
cd services
uv run python -m orbit_ml train              # synthetic + live labels when available -> artifacts/, docs/ml-metrics.json
uv run python -m orbit_ml publish            # live features -> battery_health + range:{vin}
uv run python -m orbit_ml train-and-publish  # both (what the compose `ml` profile runs)
uv run python -m orbit_ml evaluate           # re-score saved models on the held-out synthetic vehicles
```

Options: `--hours 6` (live window), `--vehicles 6000 --seed 7` (synthetic), and `--source auto|synthetic|live`.
`DATABASE_URL` and `REDIS_URL` come from the environment. The database needs migration `db/migrations/0004_ml.sql`.
Artifacts are written to `services/ml/artifacts/`, which is gitignored; override the location with `ORBIT_ML_ARTIFACTS`.

Design, metrics and limitations: [docs/ml.md](../../docs/ml.md).
