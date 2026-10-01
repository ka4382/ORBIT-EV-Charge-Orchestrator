# Load tests

- `api.js` — k6 HTTP scenario (500 VUs) against the API; thresholds p95 < 200 ms, p99 < 500 ms. `make load`.
- `ingest.js` — k6 with xk6-kafka producing raw events at a fixed arrival rate to measure ingest capacity.
- The Go simulator itself is the 100K events/s producer; `simulator/README.md` documents `make bench`.
- Soak: `SIM_DURATION_S=3600 make sim` with the `obs` profile up; watch memory panels in Grafana.

Results are written to `tests/reports/` and summarised in `docs/benchmarks.md`.
