// Builds docs/ORBIT_Solution_Document.docx following the hackathon template's section structure.
const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType,
  ShadingType, AlignmentType, ImageRun, PageBreak, LevelFormat, BorderStyle, TableOfContents,
  Footer, PageNumber, Header,
} = require('docx');

const ROOT = '/Users/bhanuprakashkusha/karthik_motorq';
const BLUE = '1F4E9A';
const GREY = '595959';
const FILL = 'F2F2F2';
const PAGE_W = 11906 - 1300 - 1300; // A4 width minus template margins, in DXA

const t = (text, o = {}) => new TextRun({ text, font: 'Calibri', size: 21, ...o });
const P = (text, o = {}) =>
  new Paragraph({ spacing: { after: 120, line: 276 }, ...o, children: Array.isArray(text) ? text : [t(text)] });
const B = (label, rest) => P([t(label, { bold: true }), t(rest)]);
const H1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 160 }, children: [new TextRun({ text, font: 'Calibri', size: 32, bold: true, color: BLUE })] });
const H2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 240, after: 120 }, children: [new TextRun({ text, font: 'Calibri', size: 26, bold: true, color: BLUE })] });
const bullet = (text) => new Paragraph({ numbering: { reference: 'bullets', level: 0 }, spacing: { after: 60 }, children: Array.isArray(text) ? text : [t(text)] });
const bb = (label, rest) => bullet([t(label, { bold: true }), t(rest)]);
const code = (text) => new Paragraph({ spacing: { after: 120 }, shading: { type: ShadingType.CLEAR, fill: FILL, color: 'auto' },
  children: text.split('\n').flatMap((line, i) => [...(i ? [new TextRun({ break: 1 })] : []), new TextRun({ text: line, font: 'Consolas', size: 17 })]) });

function table(headers, rows, widths) {
  const total = widths.reduce((a, b) => a + b, 0);
  const scale = PAGE_W / total;
  const w = widths.map((x) => Math.floor(x * scale));
  w[w.length - 1] += PAGE_W - w.reduce((a, b) => a + b, 0);
  const cell = (text, i, head) => new TableCell({
    width: { size: w[i], type: WidthType.DXA },
    shading: head ? { type: ShadingType.CLEAR, fill: FILL, color: 'auto' } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: [new Paragraph({ children: [t(String(text), { size: 18, bold: head })] })],
  });
  return new Table({
    width: { size: PAGE_W, type: WidthType.DXA },
    columnWidths: w,
    rows: [
      new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, i, true)) }),
      ...rows.map((r) => new TableRow({ children: r.map((c, i) => cell(c, i, false)) })),
    ],
  });
}
const gap = () => new Paragraph({ spacing: { after: 80 }, children: [] });

function image(file, widthPx, caption) {
  const buf = fs.readFileSync(file);
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20); // PNG IHDR
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 60 },
      children: [new ImageRun({ type: 'png', data: buf, transformation: { width: widthPx, height: Math.round(widthPx * h / w) } })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [t(caption, { italics: true, size: 18, color: GREY })] }),
  ];
}

const shots = path.join(ROOT, 'docs/screenshots/live'); // run: cd docs/solution-doc && npm i docx@9 && node build.js

const children = [
  // ---------- cover ----------
  new Paragraph({ spacing: { before: 1200, after: 120 }, children: [new TextRun({ text: 'Connected Vehicle Intelligence Hackathon', font: 'Calibri', size: 28, color: GREY })] }),
  new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text: 'ORBIT — EV Charge Orchestrator', font: 'Calibri', size: 52, bold: true, color: BLUE })] }),
  new Paragraph({ spacing: { after: 480 }, children: [new TextRun({ text: 'Solution Document', font: 'Calibri', size: 32, color: GREY })] }),
  table(['Field', 'Value'], [
    ['Submitted by', '[Team Name]'],
    ['Team members & roles', '[Name – role – email, for each member]'],
    ['Problem space chosen', 'EV charging and battery health for mixed fleets (cost-optimal charging under time-of-use tariffs, stranding prevention, battery state-of-health)'],
    ['Repository URL', '[Link]'],
    ['Demo video URL (≤ 5 min)', '[Link]'],
    ['Date of submission', '[DD/MM/YYYY]'],
  ], [30, 70]),
  new Paragraph({ children: [new PageBreak()] }),
  new TableOfContents('Table of Contents', { hyperlink: true, headingStyleRange: '1-2' }),
  new Paragraph({ children: [new PageBreak()] }),

  // ---------- 1 ----------
  H1('1. Executive Summary'),
  P('Fleet operators electrifying their vehicles pay peak tariffs when vehicles charge on arrival, strand vehicles that run out of charge between chargers, and notice battery degradation only when a pack fails. ORBIT turns a live stream of vehicle telemetry into charging decisions: it predicts range and battery health, schedules every charge into the cheapest tariff window that still meets the departure time, routes low-charge vehicles to the nearest charger they can actually reach, and raises stranding and battery alerts in under a second.'),
  P('Key results, all measured on one laptop (Apple M4, Docker Desktop with 10 CPUs):'),
  bb('Ingestion: ', '100,384 events/s from 100,000 simulated vehicles into Kafka with zero errors; 3x shift-start bursts absorbed without loss.'),
  bb('Latency: ', 'vehicle visible on the dashboard path within 100 ms at p95; critical alerts within 250 ms; API p95 29 ms and p99 77 ms at 448 requests/s.'),
  bb('Savings: ', 'the dynamic-programming scheduler is 50.5 % cheaper than charge-on-arrival in benchmarks; a live plan charged entirely at ₹6.5/kWh and saved ₹82.80 against the baseline.'),
  bb('Resilience: ', 'broker killed mid-stream for 20 s: 345,994 records buffered, 0 dropped, all consumers recovered.'),
  bb('Quality: ', '316 Python tests at 95 % coverage, 39 web tests, 9 Go packages, Testcontainers integration tests, BDD scenarios, chaos test.'),
  P('What sets it apart: a guard-railed AI agent that can only propose actions a manager approves, with every step audited; pgvector similarity that finds vehicles degrading like a known-bad pack; and profiling-driven engineering that is documented with before and after numbers.'),

  // ---------- 2 ----------
  H1('2. Problem Statement & Validation'),
  H2('2.1 Problem Statement'),
  P('An EV fleet manager needs a way to schedule and route charging across hundreds or thousands of vehicles because uncoordinated charging strands vehicles mid-shift, pays peak tariffs and hides battery degradation until a pack fails, which today costs an estimated 15–30 % of fleet energy spend plus tow-and-lost-shift costs for every stranded vehicle.'),
  B('Primary user: ', 'fleet manager of an EV or mixed fleet.'),
  B('Secondary stakeholders: ', 'fleet finance (energy cost and savings), OEM battery and warranty teams (state of health), drivers (no stranding), auditors (who did what with location data), sustainability teams.'),
  H2('2.2 Evidence & Validation'),
  table(['Evidence / Assumption', 'Source or Method', 'What It Shows', 'Confidence'], [
    ['Commercial time-of-use tariffs differ 2–3x between peak and off-peak', 'Published Indian commercial EV tariffs; modelled as ₹6.5 / ₹9.0 / ₹12.5 per kWh', 'Shifting the same kWh into off-peak windows is a direct cost lever', 'High'],
    ['Charging on arrival concentrates load in the evening peak', 'Simulation of 20,000 vehicles with shift patterns', 'Baseline policy pays peak prices; DP schedule avoids them (50.5 % cheaper)', 'High (simulated)'],
    ['A vehicle below 15 % SoC with no reachable charger becomes a strand event', 'Assumption; validated with Dijkstra on a road graph', 'Stranding needs road distance, not straight-line distance', 'Medium'],
    ['Frequent full charges and DC fast charging accelerate degradation', 'Battery literature; simulator models a 2 % fast-degrade cohort', 'SoH-aware planning and early warning add value', 'Medium'],
    ['Connected cars produce up to 25 GB/hour by 2030', 'McKinsey / S&P (problem statement)', 'Streaming architecture and edge filtering are necessary', 'High'],
  ], [28, 26, 30, 16]),
  gap(),
  P('Facts versus assumptions: tariff structures and data volumes are sourced; savings, stranding rates and degradation labels come from our simulator and are labelled as such. Validation method: simulation plus back-testing of the scheduler against a charge-on-arrival baseline.'),
  P('Existing alternatives and why they fall short:'),
  bb('OEM portals: ', 'single brand, no cross-fleet optimisation or tariff-aware scheduling.'),
  bb('Aftermarket dongles: ', 'hardware and install cost; usually no battery-level signals.'),
  bb('Motorq Fuse: ', 'flags issues such as idling and estimates dollar impact; it does not schedule charging under tariffs or depot capacity.'),
  bb('Charger-network apps: ', 'charger-centric rather than fleet-centric; no departure constraints or SoH.'),
  H2('2.3 Impact & Success Metrics'),
  table(['Metric', 'Baseline Today', 'Target', 'How Measured / Estimated'], [
    ['Energy cost per kWh charged', 'Charge on arrival', '−20 % (achieved −50.5 % in benchmark)', 'Scheduler back-test vs baseline (planner/bench.py)'],
    ['Strand events per 1,000 vehicles/day', 'No reachability check', '−70 %', 'STRAND_RISK alert + nearest reachable charger routing'],
    ['Peak depot load (kW)', 'Uncoordinated sessions', '−30 % (achieved 1,606 → 1,320 kW)', 'Depot smoothing benchmark, 300 vehicles'],
    ['Degrading packs found before SoH < 80 %', 'None', '≥ 90 % recall (achieved 0.95)', 'SoH model on held-out vehicles'],
    ['Telemetry to critical alert', 'n/a', '< 5 s (achieved ≤ 250 ms)', 'orbit_e2e_seconds histogram'],
  ], [28, 20, 24, 28]),
  gap(),
  B('Scale of impact: ', 'at ₹6 per kWh saved on ~30 kWh per vehicle per day, 10,000 vehicles save roughly ₹18 lakh a day and 100,000 vehicles roughly ₹1.8 crore a day (illustrative, from the measured tariff spread).'),
  B('Wider impact: ', 'fewer stranded vehicles (safety, driver time), lower peak grid load, longer battery life, and audited, privacy-masked use of location data (DPDP, GDPR).'),

  // ---------- 3 ----------
  H1('3. Solution Description'),
  H2('3.1 Solution Overview & User Journey'),
  P('ORBIT is a web console backed by a streaming platform. Vehicles stream telemetry; ORBIT keeps a live picture of every vehicle, watches for risk, and plans charging. The manager sees the fleet on a map, gets alerts, opens a vehicle, and plans or approves charges.'),
  P('User journey:'),
  bullet('A vehicle reports its state of charge every second (two OEM formats, normalised into one schema).'),
  bullet('Ingest validates, de-duplicates and normalises the event; the state service updates the live map; the alert service checks rules.'),
  bullet('A vehicle drops below 20 %: LOW_SOC; below 15 % with no charger within road range: STRAND_RISK, on the console in under a second.'),
  bullet('The manager opens the vehicle: range forecast, nearest reachable chargers, and a "Plan charge" form.'),
  bullet('The planner routes to the best reachable charger (Dijkstra) and schedules the charge into the cheapest tariff slots before departure (dynamic programming); the plan shows cost, baseline and savings.'),
  bullet('Or the manager asks the agent "which vehicles should charge tonight?"; the agent proposes a re-plan that the manager approves, and approval creates the plans.'),
  ...image(path.join(shots, 'console.png'), 600, 'Figure 1. Live fleet console: 12,500 vehicles streaming, KPI tiles and the live event rail (real stack, not mock data).'),
  ...image(path.join(shots, 'vehicles-plan.png'), 600, 'Figure 2. Vehicle drawer: state, model-based range forecast, nearest reachable chargers, and a READY charging plan.'),
  H2('3.2 Key Value Proposition'),
  B('Customer job: ', 'keep every vehicle charged for its next departure at the lowest energy cost, without exceeding depot capacity.'),
  B('Pain relieved: ', 'no more peak-tariff charging on arrival, no surprise strandings, no manual spreadsheet scheduling.'),
  B('Gain created: ', 'per-vehicle cost-optimal schedules with a stated saving, road-aware stranding prediction, battery-health early warning with "vehicles like this one", and an assistant that acts only with approval.'),
  B('Differentiation: ', 'optimisation and routing on live data rather than reports after the fact; brand-agnostic through OEM adapters; every decision explainable (savings, path, rationale) and audited.'),
  H2('3.3 Innovative Ideas'),
  bb('Savings-first scheduling: ', 'every plan carries its own counterfactual (charge-on-arrival cost), so finance sees savings per plan, per day and per fleet. Evidence: 50.5 % cheaper in benchmark; live plan ₹187.20 vs ₹270.00.'),
  bb('Approval-gated agent: ', 'the agent can read fleet data but can only create PENDING actions; a manager approval runs the same plan-creation path as the UI, and every query, tool call and answer is audited. Evidence: 20/20 golden questions pass, 0 ungrounded numbers.'),
  bb('Degradation look-alikes: ', 'a 32-dimension battery-profile embedding in pgvector finds the five vehicles degrading most like a given one. Evidence: fast degraders are each other\'s nearest neighbours in tests; HNSW index, tenant-scoped.'),

  // ---------- 4 ----------
  H1('4. Feature List'),
  P('Status reflects the code in the repository; video timestamps are added after recording.'),
  table(['ID', 'Feature', 'User Story', 'Priority', 'Status', 'Code Path', 'Video'], [
    ['F-01', '100K-vehicle simulator (bursts, duplicates, out-of-order, malformed)', 'As an engineer I want realistic load so that the platform is tested at scale', 'Must', 'Done', 'simulator/', '[mm:ss]'],
    ['F-02', 'Validated, de-duplicated, multi-OEM ingestion with back-pressure', 'As a platform team I want one canonical event so that every service reads one format', 'Must', 'Done', 'services/ingest/, common/adapters.py', '[mm:ss]'],
    ['F-03', 'Live vehicle state and map', 'As a fleet manager I want to see every vehicle now so that I can act', 'Must', 'Done', 'services/state/, web/src/pages/FleetConsole.tsx', '[mm:ss]'],
    ['F-04', 'Stranding, low-SoC, SoC-drop, SoH and battery-DTC alerts', 'As a fleet manager I want early warnings so that no vehicle strands', 'Must', 'Done', 'services/alert/', '[mm:ss]'],
    ['F-05', 'Nearest reachable charger (Dijkstra)', 'As a dispatcher I want chargers I can actually reach so that I route safely', 'Must', 'Done', 'services/planner/domain/graph.py', '[mm:ss]'],
    ['F-06', 'Cost-optimal schedule under time-of-use tariff (DP)', 'As finance I want the cheapest charging that meets departure so that costs drop', 'Must', 'Done', 'services/planner/domain/schedule.py', '[mm:ss]'],
    ['F-07', 'Depot capacity smoothing', 'As a depot manager I want to stay under grid capacity so that we avoid demand charges', 'Should', 'Done', 'services/planner/domain/depot.py', '[mm:ss]'],
    ['F-08', 'Secure REST API + WebSocket (OAuth2/JWT, RBAC, RLS, keyset, rate limit)', 'As a customer I want secure access so that only my tenant\'s data is visible', 'Must', 'Done', 'services/api/', '[mm:ss]'],
    ['F-09', 'Web console (map, vehicles, schedule Gantt, alerts, reports, agent)', 'As a fleet manager I want one screen for the fleet so that I can decide fast', 'Must', 'Done', 'web/', '[mm:ss]'],
    ['F-10', 'Range and SoH models vs baselines', 'As a manager I want accurate range so that plans are realistic', 'Should', 'Done', 'services/ml/', '[mm:ss]'],
    ['F-11', 'pgvector "similar degradation" search', 'As a battery engineer I want look-alike vehicles so that I can inspect them early', 'Should', 'Done', 'services/ml/embeddings.py, db/migrations/0004_ml.sql', '[mm:ss]'],
    ['F-12', 'Approval-gated AI agent with audit trail', 'As a manager I want to ask questions and approve actions so that I save time safely', 'Should', 'Done', 'services/api/orbit_api/agent/', '[mm:ss]'],
    ['F-13', 'Privacy: location masking, retention, right to erasure, access audit', 'As an auditor I want compliant data handling so that we meet DPDP and GDPR', 'Must', 'Done', 'api/masking.py, routers/privacy.py, audit.py', '[mm:ss]'],
    ['F-14', 'Observability: metrics, logs, traces, Grafana dashboards', 'As SRE I want to see throughput and lag so that I can troubleshoot', 'Must', 'Done', 'infra/observability/', '[mm:ss]'],
    ['F-15', 'Parquet lake + batch analytics', 'As an analyst I want history cheaply so that I can report over months', 'Must', 'Partial', 'services/archiver/ (lake written; DuckDB/Spark reports planned)', '[mm:ss]'],
    ['F-16', 'Carbon report per route', 'As sustainability I want emissions per route so that we can cut them', 'Could', 'Planned', '—', '—'],
  ], [7, 22, 26, 9, 9, 20, 7]),

  // ---------- 5 ----------
  H1('5. Solution Architecture (High-Level Design)'),
  H2('5.1 Architecture Overview'),
  P('System context (C4 level 1): fleet managers, finance and auditors use ORBIT over HTTPS and WebSocket; OEM clouds (simulated) send telemetry over MQTT/Kafka; an OIDC identity provider issues JWTs; the Claude API backs the agent; S3-compatible object storage holds the Parquet lake. Mermaid sources: docs/architecture/c4-context.mmd, c4-container.mmd, er.mmd and two sequence diagrams.'),
  P('Containers (C4 level 2), with the protocol on each hop:'),
  table(['From → To', 'Protocol', 'Purpose'], [
    ['Simulator → Kafka telemetry.raw', 'Kafka (lz4, key = VIN); MQTT 8883 option via EMQX', '100K events/s, two OEM payload formats'],
    ['Kafka → ingest → telemetry.canonical / dlq', 'Kafka consumer group, batched send', 'Validate, normalise, de-duplicate'],
    ['canonical → state → Redis', 'Kafka → Redis pipelines', 'Latest state per VIN, geo index, partial summaries'],
    ['canonical → alert → alerts topic, Postgres, Redis pub/sub', 'Kafka, SQL, pub/sub', 'Rules, < 5 s critical alerts'],
    ['canonical → archiver → Timescale, MinIO/S3', 'COPY, S3 API', 'Hot store and Parquet lake'],
    ['API → plans.requested → planner → Postgres, plans.ready', 'Kafka, SQL', 'Dijkstra + DP scheduling'],
    ['Browser ↔ API', 'HTTPS (REST), WebSocket', 'Console, live updates'],
    ['API agent → Claude API', 'HTTPS', 'Tool-use loop (offline mode without a key)'],
  ], [36, 30, 34]),
  gap(),
  P('Data flow of one event, with measured latency: vehicle → Kafka raw → ingest (≤ 50 ms p95 from vehicle timestamp) → canonical → state in Redis (≤ 100 ms p95) → WebSocket push (throttled to one per vehicle per 2 s); in parallel alert rule → Postgres + pub/sub (≤ 250 ms).'),
  H2('5.2 Technology Stack & Justification'),
  table(['Layer', 'Choice', 'Why This, and What You Rejected'], [
    ['Ingestion / Messaging', 'MQTT (EMQX) at the edge; Kafka protocol (Redpanda in dev, managed Kafka in cloud)', 'Partitioned, replayable log with per-VIN ordering; rejected RabbitMQ (no replay, weaker key ordering) — ADR-001'],
    ['Stream / Batch Processing', 'Python asyncio consumer services; Parquet + DuckDB/Spark for batch', 'Simple, testable, horizontally scalable via consumer groups; rejected Flink for now (operational weight) — ADR-005'],
    ['Relational / NoSQL / Cache / Search / Vector', 'PostgreSQL 16 + TimescaleDB + pgvector; Redis 7; MinIO/S3', 'One CP engine for 3NF core, time series and vectors; Redis as AP cache; rejected Cassandra + Qdrant (three engines, same value at this scale) — ADR-002'],
    ['Backend / Frontend', 'FastAPI (asyncpg, raw SQL repositories); React 18 + TypeScript + MapLibre + Recharts', 'Async, OpenAPI for free, fast iteration; rejected Spring Boot for team speed and an ORM to keep SQL explicit and N+1-free'],
    ['ML / AI', 'scikit-learn (HistGradientBoosting, GradientBoosting), pgvector HNSW; Anthropic SDK tool-use loop', 'Explainable, fast models that beat baselines; agent with allowlisted tools and approval gate'],
    ['Infrastructure / CI-CD / Observability', 'Docker Compose, Helm, Terraform (AWS + GCP), GitHub Actions; Prometheus, Grafana, Loki, Tempo, OpenTelemetry', 'Same chart on any Kubernetes; open standards, no vendor lock-in'],
  ], [22, 34, 44]),
  H2('5.3 Data Architecture'),
  P('ER diagram (docs/architecture/er.mmd): tenant, oem, vehicle_model, depot, fleet, vehicle, driver, driver_assignment, charger, tariff, tariff_slot, charging_plan, charging_slot, charging_session, alert, subscription, user_account, audit_log, battery_health, agent_action — in third normal form. Deliberate denormalisation: telemetry stores vin (no join on the write path); alert.payload is JSONB (one table for all alert types); live vehicle state is a JSON document in Redis; charging_plan and battery_health carry tenant_id (filled by trigger) so row-level security applies without joins.'),
  table(['Data', 'Store', 'CAP', 'Reason'], [
    ['Fleet, vehicle, plans, slots, sessions, subscriptions, audit, agent actions', 'PostgreSQL (RLS)', 'CP', 'Ownership and money need ACID and tenant isolation'],
    ['Raw and canonical telemetry stream', 'Kafka', 'AP, durable', 'Replay, decoupling, at-least-once with idempotent consumers'],
    ['Latest state, geo index, summaries, rate limits, range coefficients', 'Redis', 'AP', 'Sub-millisecond reads; rebuildable from Kafka'],
    ['Telemetry, hot 30 days', 'TimescaleDB hypertable', 'CP', 'Range queries, compression, continuous aggregates'],
    ['Telemetry, warm and cold', 'Parquet on S3/MinIO', 'AP', 'Cheap, scannable, lifecycle tiers'],
    ['Battery-profile embeddings', 'pgvector (HNSW)', 'CP', 'Similarity co-located with vehicle rows'],
  ], [36, 20, 12, 32]),
  gap(),
  P('Capacity at 100,000 vehicles, 1 event/s each: 100K events/s (300K/s bursts); ~1 KB JSON, ~250 B Avro, ~60 B compressed Parquet. Kafka key = VIN, 48 partitions; hypertable chunked hourly and space-partitioned by VIN (16); Parquet partitioned dt/hour/tenant. Hot 30 days in Timescale (compressed after 24 h), warm 90 days in S3 Standard-IA, cold to Glacier Instant Retrieval, expiry at 2 years (Terraform lifecycle rules).'),
  table(['Query', 'Before (ms)', 'After (ms)', 'Change Made'], [
    ['Alert feed, page 50 for a tenant', '89.4', '0.55', 'OFFSET → keyset on (ts, id) with composite index'],
    ['Open CRITICAL alerts per tenant', '122.2', '1.16', 'Partial index WHERE acked_ts IS NULL (index-only scan)'],
    ['Vehicle page with model and OEM', '10.9', '2.9', 'N+1 correlated subqueries → one join + keyset index'],
    ['Latest state of every vehicle in a fleet', '804', '< 1', 'Moved to Redis projection (MGET) — ADR-004'],
  ], [34, 14, 14, 38]),
  H2('5.4 Deployment View'),
  P('Kubernetes via the Helm chart in infra/helm/orbit: one Deployment per service with HPA (ingest 3–12, state 2–8, alert 2–8, archiver 2–6, API 3–20 replicas), PodDisruptionBudgets, a default-deny NetworkPolicy, non-root read-only containers, an Ingress for /api, /api/v1/ws and the web app, and secrets from Kubernetes Secrets fed by External Secrets (Vault or a cloud secret manager). Terraform provisions AWS (EKS, MSK, RDS, ElastiCache, S3 with lifecycle and KMS) and a GCP parity skeleton (GKE Autopilot, Cloud SQL, Memorystore, GCS); both validate.'),
  B('Cloud-agnostic approach: ', 'application code uses only the Kafka protocol, Postgres, Redis and the S3 API. Moving clouds changes only values-aws.yaml or values-gke.yaml; locally, docker compose runs the same images.'),

  // ---------- 6 ----------
  H1('6. Low-Level Design'),
  H2('6.1 Layering & Separation of Concerns'),
  P('Every Python service uses hexagonal (ports and adapters) layering: domain/ holds pure logic with no I/O, application/ orchestrates use cases against Protocol ports, infrastructure/ holds Kafka, Redis, Postgres and S3 adapters, and __main__.py wires them. This keeps the algorithms unit-testable with fakes (95 % coverage) and lets us swap stores without touching rules.'),
  table(['Layer', 'Responsibility', 'Must Not'], [
    ['Presentation / API', 'HTTP, WebSocket, validation, auth checks, DTO mapping (orbit_api/routers, schemas)', 'Contain business rules or SQL'],
    ['Application / Service', 'Use cases, transactions, orchestration (e.g. plan_vehicle.py, plan_requests.py)', 'Depend on a specific database or broker'],
    ['Domain', 'Entities and rules (rules.py, schedule.py, graph.py, state.py)', 'Import framework or infrastructure code'],
    ['Infrastructure', 'Repositories, broker clients, caches (postgres.py, redis_store.py, kafka.py)', 'Leak vendor types into the domain'],
  ], [22, 50, 28]),
  gap(),
  code('contracts/  db/  simulator/{cmd,internal}  web/{src,public}\nservices/{common,ingest,state,alert,archiver,planner,ml,api}/{orbit_*,tests}\ninfra/{helm,terraform,observability}  tests/{acceptance,load,chaos,sql,agent_eval}  docs/{adr,architecture,screenshots}'),
  H2('6.2 Design Principles Applied'),
  bb('SOLID: ', 'repositories behind Protocol interfaces (dependency inversion) so tests use fakes; one service per concern (single responsibility); OEM adapters registered at runtime (open/closed).'),
  bb('12-Factor: ', 'configuration only from the environment (orbit_common/config.py); stateless processes; disposable containers that exit on fatal errors and are restarted.'),
  bb('Idempotency: ', '(VIN, seq) idempotency key, rolling Bloom filter, unique index on (vin, seq, ts), seq-monotonic Redis writes, rebalance-safe commits.'),
  bb('Fail-fast and least privilege: ', 'Pydantic validation at the edge; orbit_app database role subject to RLS; non-root containers with dropped capabilities.'),
  H2('6.3 Design Patterns Used'),
  table(['Pattern', 'Problem It Solves in Your System', 'Location in Code'], [
    ['Adapter', 'Maps each OEM payload (flat ISO / nested imperial epoch) to one canonical event', 'services/common/orbit_common/adapters.py'],
    ['Repository', 'Hides SQL behind interfaces; tests use in-memory fakes', 'services/api/orbit_api/repositories/'],
    ['Strategy', 'LLM agent vs deterministic offline agent behind one interface', 'services/api/orbit_api/agent/'],
    ['CQRS', 'Writes go through Kafka and Postgres; reads of live state come from the Redis projection', 'services/state/, api/state.py'],
    ['Observer / pub-sub', 'State and alerts pushed to WebSocket clients per tenant', 'Redis events:{tenant}, routers/ws.py'],
    ['Retry with backoff', 'Planner waits for a plan row published before its INSERT commits', 'planner/application/plan_vehicle.py'],
    ['Back-pressure', 'Ingest pauses partitions when producer buffers are full; simulator blocks on full buffer', 'ingest/application/runner.py, simulator/internal/transport'],
  ], [18, 52, 30]),
  H2('6.4 Interfaces, Contracts & Runtime Flows'),
  P('REST and WebSocket contract: contracts/api.md and docs/openapi.json (versioned /api/v1, RFC 7807 errors, opaque keyset cursors, 100 requests/min per user). Event schemas: contracts/telemetry.canonical.v1.avsc (Avro, backward-compatible, add-only optional fields), contracts/topics.yaml (topics, keys, partitions, retention), contracts/oem-formats.md, contracts/redis.md.'),
  P('Sequence diagrams: docs/architecture/seq-alert.mmd (event to alert to acknowledgement) and seq-broker-failure.mmd (broker down: producer buffers, consumers resume from committed offsets, duplicates removed). Duplicate events are dropped by the Bloom filter and again by the unique index; an unknown OEM format goes to telemetry.dlq.'),
  H2('6.5 Algorithms & Data Structures'),
  table(['Problem', 'Algorithm', 'Complexity', 'Scale Tested'], [
    ['Nearest reachable charger', 'Dijkstra with early exit on a 60×60 road lattice', 'O((V′+E′) log V′)', '0.20 ms median, 40 chargers, k = 5'],
    ['Cost-optimal schedule', 'DP over (15-min slot, 1 % SoC bucket, 3 power levels)', 'O(T·S·P)', '2.27 ms for 96 × 101 × 3'],
    ['Depot capacity', 'Greedy slack-first smoothing (heuristic; exact is NP-hard)', 'O(passes·T·V)', '300 vehicles: 1.0 ms, 1,606 → 1,320 kW'],
    ['Trip and stop segmentation', 'Single-pass change-point detector', 'O(n)', '100,000 samples in 41 ms'],
    ['VIN validation', 'Regex + ISO 3779 check digit (cached)', 'O(1)', 'Every event'],
    ['DTC parsing', 'Regex [PCBU][0-3][0-9A-F]{3}', 'O(n)', 'Every event'],
    ['Dedupe at 100K/s', 'Two-generation rolling Bloom filter, p = 0.1 %', 'O(k)', '3.8 µs/event; FP < 0.5 %'],
    ['Top-K fault codes', 'Count-Min sketch + bounded heap', 'O(d)', 'Metric orbit_dtc_topk'],
    ['End-to-end plan', 'Dijkstra + DP per vehicle', '—', '10,000 vehicles in 14.9 s (1.49 ms each)'],
  ], [24, 34, 18, 24]),
  gap(),
  P('Pseudocode, full complexity notes and a write-up of a quadratic slowdown found and fixed by the 10,000-vehicle benchmark are in docs/algorithms.md.'),

  // ---------- 7 ----------
  H1('7. Non-Functional Requirements & Performance Benchmarks'),
  table(['NFR', 'Target (Case Study)', 'Achieved', 'How Measured'], [
    ['Ingest throughput', '100K+ events/s', '100,384 events/s to Kafka, 0 errors; 3x bursts generated at ~299K/s', 'Simulator counters, 20 s window'],
    ['End-to-end latency', '< 2 s dashboard; < 5 s critical alert', 'State ≤ 100 ms p95; alert ≤ 250 ms', 'orbit_e2e_seconds histograms, 30 s window'],
    ['API latency', 'p95 < 200 ms; p99 < 500 ms', 'p95 29 ms, p99 77 ms at 448 req/s', 'tests/load/api_latency.py, 300 users, 60 s'],
    ['Resilience', 'Recovers after broker/pod failure', 'Broker killed 20 s: 0 records dropped, 7/7 consumers up', 'tests/chaos/kill_broker.sh'],
    ['Availability', '99.9 %, no single point of failure', 'Stateless replicas, PDBs, HPA, restart policies; single broker in dev', 'Helm chart design'],
  ], [18, 24, 34, 24]),
  gap(),
  B('Load test setup: ', 'one laptop (Apple M4, Docker Desktop 10 CPUs, 8 GB); full stack in docker compose. Producer and broker sustain 100K/s; the Python stream tier processes ~24K (ingest), ~18K (state) and ~30K (alert, archiver) events/s per core, so 100K/s end to end needs about 19 cores — a cluster deployment sized in docs/benchmarks.md. The live demo runs 20K events/s with consumer lag under one second.'),
  B('Engineering from measurements: ', 'profiling raised state throughput from ~5K to ~18K events/s (single JSON SET instead of a 14-field HSET), ingest from ~12K to ~24K per replica (batched sends, cached VIN checks), and fixed a 1-partition topic, a rebalance crash loop, producer data loss during outages, and a commit-before-response race.'),

  // ---------- 8 ----------
  H1('8. Security & Compliance'),
  table(['Threat (STRIDE)', 'Control'], [
    ['Spoofed device sends fake data (S)', 'mTLS per device on EMQX, VIN bound to certificate; VIN check digit and schema validation'],
    ['Replayed or duplicated events (T)', 'Per-vehicle seq, Bloom de-duplication, unique index, seq-monotonic state writes, timestamp skew check'],
    ['Cross-tenant data access (I)', 'tenant_id only from the verified JWT; Postgres row-level security on every tenant table; tenant-prefixed Redis keys; integration test proves isolation'],
    ['API flood / burst (D)', 'Redis token bucket (100/min per user), Kafka buffering, ingest back-pressure, HPA'],
    ['Prompt injection makes the agent act (E)', 'Tool allowlist, tenant injected server-side, tool output marked untrusted data, approval required for any write, full audit'],
  ], [34, 66]),
  gap(),
  bb('Authentication and authorisation: ', 'OAuth2/OIDC JWT (RS256 via JWKS in production, HS256 dev issuer locally); roles admin, manager, finance, auditor; Postgres RLS by tenant.'),
  bb('Encryption and secrets: ', 'TLS at the ingress, SASL/TLS to managed Kafka, KMS-encrypted RDS and S3; secrets via External Secrets; .env never committed; gitleaks in CI.'),
  bb('Privacy (GDPR, DPDP Act 2023): ', 'driver names stored masked and licences hashed; finance and auditor roles see location rounded to ~1 km; telemetry retained 30 days hot then tiered; right-to-erasure endpoint masks the driver, removes assignments and writes an ERASURE audit row; every privileged data access is audited.'),
  bb('AI safety: ', 'manager/admin only; 5 tool calls, 7 model calls and a $0.10 cost cap per query; 1,000-character question limit; refusal of injection-looking input in offline mode; AGENT_QUERY, AGENT_TOOL and AGENT_ANSWER audit rows.'),

  // ---------- 9 ----------
  H1('9. Test Strategy'),
  table(['Test Type', 'Tools', 'No. of Tests', 'Coverage / Result', 'In CI?'], [
    ['Unit', 'pytest, vitest, go test', '316 Python, 39 web, 9 Go packages', '95 % Python line coverage; all pass', 'Yes'],
    ['Integration & Contract', 'Testcontainers (Redpanda, Timescale, Redis); contract tests on payloads', '7 (4 stream pipeline, 3 API/RLS)', 'All pass against real containers', 'Yes'],
    ['Acceptance (BDD)', 'behave', '6 scenarios (5 run, 1 marked WIP)', '5/5 pass, 8 of 8 consecutive runs', 'Yes'],
    ['Performance / Load / Soak', 'Go simulator, Python load generator, k6 scripts', 'Producer 100K/s; API 300 users', 'p95 29 ms, p99 77 ms', 'Nightly'],
    ['Security', 'Semgrep, Trivy, OWASP ZAP baseline, gitleaks', 'Workflow jobs', 'Configured in CI; not executed locally', 'Yes'],
    ['Compliance & Chaos', 'pytest (audit, erasure), chaos script', 'Audit/erasure tests; broker kill', 'Chaos PASS: 0 records dropped', 'Nightly'],
  ], [18, 26, 20, 24, 12]),
  gap(),
  B('Edge cases covered: ', 'duplicate (VIN, seq), out-of-order events, future timestamps, unknown OEM format, invalid VIN, out-of-range values, broker outage, consumer-group rebalance, Redis loss (state rebuilt from Kafka), plan published before its row commits, infeasible charging targets, cross-tenant access, prompt injection.'),
  B('Reports: ', 'tests/reports/ (coverage, chaos.json, api_latency.json, EXPLAIN plans); docs/benchmarks.md summarises them.'),

  // ---------- 10 ----------
  H1('10. Observability'),
  P('Every service exposes Prometheus metrics (events in/out, drops by reason, batch latency, end-to-end latency, consumer lag, planner duration, top fault codes), writes structured JSON logs (shipped to Loki by Promtail), and propagates OpenTelemetry traces to Tempo. Grafana dashboards are provisioned from infra/observability/grafana/dashboards (Ingest & Stream; API & Planner). Start with make obs or make run.'),
  B('Troubleshooting a latency spike: ', 'check the consumer-lag panel to find which group is behind; compare events-in against the simulator rate to tell a burst from a slowdown; open the end-to-end latency histogram for that service; filter Loki logs by service and level for errors (for example commit failures during a rebalance); follow a trace from ingest to alert in Tempo. This is how we found the single-partition topic and the rebalance crash loop.'),

  // ---------- 11 ----------
  H1('11. AI / ML Component'),
  B('Purpose: ', 'the range model decides whether a vehicle can reach a charger and how much to charge; the SoH model flags packs heading below 80 %; embeddings find vehicles degrading the same way. Rules alone use nominal range times SoC, which ignores driving style and degradation (11.9 km error versus 2.9 km).'),
  B('Data and features: ', 'per-vehicle features from TimescaleDB (SoC trajectory, km, kWh, speed profile, temperature, fast-charge ratio, odometer, model). Labels are synthetic and follow the simulator\'s physics; the split is by vehicle (SHA-256 of VIN) with a leakage check that fails on overlap.'),
  table(['Model', 'Metric', 'Model', 'Baseline'], [
    ['Remaining range', 'MAE', '2.93 km', '11.94 km'],
    ['Remaining range', 'MAPE', '2.30 %', '9.81 %'],
    ['SoH in 30 days', 'MAE', '0.11 pts', '0.27 pts'],
    ['SoH in 30 days', 'Recall, SoH < 80 %', '0.95', '0.60'],
    ['SoH in 30 days', 'Precision, SoH < 80 %', '0.94', '1.00'],
  ], [28, 28, 22, 22]),
  gap(),
  B('Model and agent design: ', 'HistGradientBoosting and GradientBoosting (scikit-learn); 32-dimension L2-normalised embeddings in pgvector with an HNSW index and tenant-scoped search; per-vehicle range coefficients published to Redis so the API serves forecasts without loading a model. The agent uses seven read tools and one write tool (propose_replan → PENDING actions only); it runs a Claude tool-use loop when ANTHROPIC_API_KEY is set and a deterministic offline router otherwise.'),
  B('Evaluation: ', 'held-out vehicles (1,201) for the models; 20 golden questions for the agent: 20/20 pass and 0 answers contain numbers absent from tool results (offline mode; LLM mode tested with a fake client).'),
  B('Guardrails and cost: ', 'live run published 97,434 battery-health rows with embeddings and 97,434 range coefficients; training 4.5 s, scoring 100K vehicles 0.35 s. Agent: offline $0 and 5–30 ms; LLM estimate ~$0.015 and 3–8 s per question, capped at $0.10. Limitation: metrics show recovery of simulated physics, not real-world accuracy.'),

  // ---------- 12 ----------
  H1('12. Architecture Decisions, Risks & Future Enhancements'),
  table(['ADR', 'Decision', 'Consequence'], [
    ['ADR-001', 'Kafka protocol (Redpanda dev, managed Kafka prod) over RabbitMQ', 'Replay and per-VIN ordering; consumers must be idempotent'],
    ['ADR-002', 'PostgreSQL + TimescaleDB + pgvector over Cassandra + Qdrant', 'One CP engine; single-writer ceiling mitigated by batching and sampling'],
    ['ADR-003', 'Telemetry AP / at-least-once; plans, billing, audit CP', 'Bursts and outages become lag, never loss; dashboards may be seconds stale'],
    ['ADR-004', 'Redis latest-state projection with TTL and seq-monotonic writes', 'Sub-ms reads; time-based invalidation; DB stays source of truth for history'],
    ['ADR-005', 'Python asyncio consumers now, Flink later', 'Simple and testable; ~20–30K events/s per core, so 100K/s needs more replicas'],
  ], [12, 48, 40]),
  gap(),
  P('Risks and technical debt:'),
  bullet('100K events/s end to end needs ~19 cores for the stream tier; measured on a laptop only at 20K/s live.'),
  bullet('ML labels are synthetic; real OEM data is needed to claim real-world accuracy.'),
  bullet('Agent LLM mode untested against the live API (no key available); offline mode is the default.'),
  bullet('Security scans (Semgrep, Trivy, ZAP) are configured in CI but were not run locally; the stranding BDD scenario needs a deterministic simulator injection.'),
  bullet('Running the full stack needs ~20 GB free host disk; during development the disk filled twice, which led to retention caps and a preflight check.'),
  P('Next three steps from prototype to pilot:'),
  bullet('Connect one real OEM feed through a new adapter and retrain the range model on real trips.'),
  bullet('Deploy to EKS with Terraform and Helm, run the 100K/s load and soak tests on the cluster, and move the alert service to Flink if windows grow.'),
  bullet('Pilot with one depot: compare a month of planned versus actual energy cost and strand events.'),

  // ---------- 13 ----------
  H1('13. Demo Video (5 Minutes Maximum)'),
  table(['Time', 'Segment', 'What to Show'], [
    ['0:00 – 0:30', 'Problem', 'A fleet manager paying peak tariffs and a vehicle about to strand; the 2–3x tariff spread'],
    ['0:30 – 1:00', 'Solution', '"Every vehicle charged, on time, at the lowest tariff" — landing page and console'],
    ['1:00 – 3:00', 'Live Demo', 'Live map; a LOW_SOC or STRAND_RISK alert arriving; open the vehicle; nearest reachable chargers; plan a charge and show ₹ savings; ask the agent and approve its proposal'],
    ['3:00 – 4:15', 'Under the Hood', 'Architecture; Grafana lag and throughput; kill the broker and show 0 records dropped; API latency numbers'],
    ['4:15 – 5:00', 'Impact & Next Steps', '50.5 % cheaper charging, < 250 ms alerts, 95 % test coverage; pilot plan; team'],
  ], [16, 20, 64]),
  gap(),
  P('Video link: [URL]'),

  // ---------- 14 ----------
  H1('14. Repository Checklist'),
  bb('README: ', 'problem, architecture, quick start, environment variables, test commands, known issues — done.'),
  bb('One-command run: ', 'make run (preflight disk check, then docker compose with simulator and observability) — done.'),
  bb('Structure: ', 'service folders plus /docs, /infra and /tests — done.'),
  bb('CI pipeline: ', '.github/workflows/ci.yml (lint, type check, unit, integration, Go, web, security scans, images, acceptance, Terraform and Helm validation) and nightly load and chaos — configured.'),
  bb('Hygiene: ', '.env ignored, .env.example provided, secrets only from the environment — done; commits from all members — team to confirm.'),
  bb('Final tag: ', 'v1.0-submission — to be created at code freeze.'),

  // ---------- 15 ----------
  H1('15. Conclusion'),
  bb('Key learnings: ', 'measure before optimising — every large gain came from a profile or a failed test, not a guess; chaos and acceptance tests found real defects that unit tests could not.'),
  bb('Strengths: ', 'complete loop from telemetry to an approved, cost-optimal action; tenant isolation enforced in the database; honest, reproducible benchmarks.'),
  bb('Challenges solved: ', 'single-partition topic, rebalance crash loop, producer loss during outages, plan published before commit, read-your-writes on POST, summary over-counting across replicas, and two host-disk exhaustion incidents.'),

  // ---------- 16 ----------
  H1('16. Declarations'),
  bb('Open-source components: ', 'listed with versions in docs/sbom.json (112 Python, 51 Go, 91 web runtime packages). Main components and licences: PostgreSQL (PostgreSQL), TimescaleDB (Timescale License for the community edition image), pgvector (PostgreSQL), Redis 7 (BSD-3 in 7.2 and earlier; RSALv2/SSPL from 7.4), Redpanda (BSL 1.1), EMQX (Apache-2.0 in 5.x open source; BSL from 5.9), MinIO (AGPL-3.0, used via the Bitnami legacy image), FastAPI, aiokafka, asyncpg, scikit-learn, React, MapLibre GL (BSD-3), Recharts (MIT), franz-go (BSD-3), Grafana, Loki, Tempo (AGPL-3.0), Prometheus (Apache-2.0). Licences should be confirmed against the exact versions before any commercial use.'),
  bb('AI tools used: ', 'Claude Code (Anthropic) was used as a coding assistant for scaffolding, implementation, tests, profiling and documentation; the team reviewed the design and results. The product\'s agent uses the Anthropic Claude API when a key is configured.'),
  bb('Data: ', 'all data is synthetic, generated by the repository\'s simulator and seed script; no real personal or vehicle-owner data is used. Brand imagery in the UI is limited to abstract illustrations and icons; no third-party logos are shipped.'),

  // ---------- 17 ----------
  H1('17. Appendix'),
  bullet('docs/PLAN.md — full plan; docs/DESIGN.md — design system; docs/adr/ — five ADRs.'),
  bullet('docs/architecture/ — C4 context and container, ER, sequence diagrams (Mermaid).'),
  bullet('docs/benchmarks.md, docs/sql-optimisation.md, docs/algorithms.md, docs/ml.md, docs/agent.md, docs/threat-model.md.'),
  bullet('docs/screenshots/live/ — screenshots of the running system; tests/reports/ — test evidence.'),
  ...image(path.join(shots, 'schedule.png'), 600, 'Figure 3. Depot schedule: charging sessions per charger against tariff bands and depot capacity.'),
];

const doc = new Document({
  creator: 'ORBIT team', title: 'ORBIT — Solution Document',
  styles: { default: { document: { run: { font: 'Calibri', size: 21 } } } },
  features: { updateFields: true },
  numbering: { config: [{ reference: 'bullets', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1440, right: 1300, bottom: 1300, left: 1300, header: 600, footer: 600 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [t('ORBIT — Connected Vehicle Intelligence Hackathon', { size: 16, color: GREY })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: ['Page ', PageNumber.CURRENT], font: 'Calibri', size: 16, color: GREY })] })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  const out = path.join(ROOT, 'docs/ORBIT_Solution_Document.docx');
  fs.writeFileSync(out, buf);
  console.log('wrote', out, buf.length, 'bytes');
});
