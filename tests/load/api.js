// k6 load test for the ORBIT API (docs/PLAN.md §10: p95 < 200 ms, p99 < 500 ms at 500 VUs).
//
//   ORBIT_TOKEN=<dev jwt> k6 run tests/load/api.js            # single shared token
//   API_URL=http://localhost:8000 k6 run tests/load/api.js     # dev mode: one token per VU (avoids the 100 req/min/sub limit)
//
// The API rate-limits 100 requests / minute per JWT subject, so with a single ORBIT_TOKEN most requests will
// be 429 at 500 VUs. In dev (ORBIT_ENV=dev) the script mints one token per VU via POST /auth/dev-token, which
// keeps each subject under the limit at ~1.4 req/s.
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate } from "k6/metrics";

const API = (__ENV.API_URL || "http://localhost:8000") + "/api/v1";
const VUS = Number(__ENV.VUS || 500);
const TENANT = Number(__ENV.TENANT_ID || 1);
const SHARED_TOKEN = __ENV.ORBIT_TOKEN || "";

export const options = {
  scenarios: {
    fleet: { executor: "constant-vus", vus: VUS, duration: __ENV.DURATION || "60s" },
  },
  thresholds: {
    http_req_duration: ["p(95)<200", "p(99)<500"],
    "http_req_duration{endpoint:summary}": ["p(95)<200"],
    "http_req_duration{endpoint:vehicles}": ["p(95)<200"],
    "http_req_duration{endpoint:alerts}": ["p(95)<200"],
    "http_req_duration{endpoint:state}": ["p(95)<200"],
    rate_limited: ["rate<0.01"],
    checks: ["rate>0.99"],
  },
};

const rateLimited = new Rate("rate_limited");
const stateLatency = new Trend("vehicle_state_ms", true);

function auth(token) {
  return { headers: { Authorization: `Bearer ${token}` } };
}

export function setup() {
  const tokens = [];
  if (SHARED_TOKEN) {
    tokens.push(SHARED_TOKEN);
  } else {
    for (let i = 0; i < VUS; i++) {
      const r = http.post(`${API}/auth/dev-token`,
        JSON.stringify({ tenant_id: TENANT, role: "manager", sub: `k6-vu-${i}` }),
        { headers: { "Content-Type": "application/json" } });
      if (r.status !== 200) throw new Error(`dev-token failed (${r.status}): set ORBIT_TOKEN or run with ORBIT_ENV=dev`);
      tokens.push(r.json("access_token"));
    }
  }
  const page = http.get(`${API}/vehicles?limit=200`, auth(tokens[0]));
  const vins = page.status === 200 ? page.json("items").map((v) => v.vin) : [];
  if (vins.length === 0) throw new Error("no vehicles visible for the token's tenant; seed the database first");
  return { tokens, vins };
}

export default function (data) {
  const token = data.tokens[(__VU - 1) % data.tokens.length];
  const vin = data.vins[Math.floor(Math.random() * data.vins.length)];
  const params = (endpoint) => Object.assign({ tags: { endpoint } }, auth(token));

  const calls = [
    ["summary", `${API}/fleet/summary`],
    ["vehicles", `${API}/vehicles?limit=50`],
    ["alerts", `${API}/alerts?limit=50`],
    ["state", `${API}/vehicles/${vin}/state`],
  ];
  const [endpoint, url] = calls[__ITER % calls.length];
  const res = http.get(url, params(endpoint));
  rateLimited.add(res.status === 429);
  if (endpoint === "state") stateLatency.add(res.timings.duration);
  check(res, {
    "status is 2xx or 404 (no live state yet)": (r) => (r.status >= 200 && r.status < 300) || (endpoint === "state" && r.status === 404),
    "problem+json on error": (r) => r.status < 400 || (r.headers["Content-Type"] || "").includes("application/problem+json"),
    "has request id": (r) => !!r.headers["X-Request-Id"],
  });
  sleep(0.7); // ~1.4 req/s per VU keeps each subject under 100 req/min
}
