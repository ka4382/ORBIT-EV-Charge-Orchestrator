# Algorithms & Data Structures  (Solution template §6.5, PDF §9)

Every algorithm below is implemented in the repo, unit-tested, and benchmarked on a laptop
(Apple Silicon, Python 3.12, single core). The "Scale tested" table at the end holds the measured numbers;
`cd services && uv run python planner/bench.py` reproduces them.

| # | Problem | Algorithm | Time | Space | Where |
|---|---|---|---|---|---|
| 1 | Nearest reachable charger under remaining range | Dijkstra with early exit on a synthetic road lattice | O((V'+E') log V') | O(V) | `services/planner/orbit_planner/domain/graph.py` |
| 2 | Cost-optimal charging schedule under time-of-use tariff | Dynamic programming over (slot, SoC bucket) | O(T × S × P) | O(T × S) | `services/planner/orbit_planner/domain/schedule.py` |
| 3 | Depot grid-capacity smoothing | Greedy slack-first post-pass (heuristic; exact problem NP-hard) | O(passes × T × V × T log T) worst | O(V × T) | `services/planner/orbit_planner/domain/depot.py` |
| 4 | Trip / stop segmentation of noisy GPS | Single-pass two-state change-point detector | O(n) | O(1) extra | `services/planner/orbit_planner/domain/trips.py` |
| 5 | VIN validation | Regex + ISO 3779 check digit | O(1) | O(1) | `services/common/orbit_common/vin.py` |
| 6 | DTC parsing | Regex + lookup table | O(1) / O(n) scan | O(1) | `services/common/orbit_common/dtc.py` |
| 7 | Dedupe at 100K events/s | Rolling (two-generation) Bloom filter | O(k) | O(m) bits | `services/common/orbit_common/streaming.py` |
| 8 | Top-K DTCs by frequency | Count-Min sketch + bounded top dict | O(d) update | O(w × d) | `services/common/orbit_common/streaming.py` |
| 9 | Charger / vehicle proximity | Geohash (precision 6) + Redis GEO | O(1) encode, O(log N + M) search | O(1) | `services/common/orbit_common/geo.py`, Redis `geo:*` |

---

## 1. Nearest reachable charger — Dijkstra with early exit

**Problem.** Given a vehicle's position and remaining range (km), find the *k* chargers with the smallest
*road* distance that are actually reachable. Straight-line distance is not enough: rivers, one-way detours
and congestion make road distance 1–1.6× the haversine distance, and a charger 12 km away as the crow flies
may be 20 km by road and outside range. Used by `POST /plans` (charger choice), `GET /vehicles/{vin}/nearest-chargers`,
and by the alert service's `STRAND_RISK` rule (`any_reachable`).

**Data structure.** A synthetic road network is built per city on first use and cached in a module-level
LRU (`graph_for_city`, 32 cities): an N×N lattice (default 60×60 = 3 600 nodes) over a 0.6°×0.6° box centred on
the city, with 8-neighbour ("king move") edges. Each edge's weight is `haversine_km × U[1.0, 1.6]` from a RNG
seeded from the city centre, so the graph is reproducible across processes. Two "rivers" (row cuts) remove
every crossing edge except straight bridges every 12 columns, so road distance genuinely diverges from
straight-line distance. Adjacency is a list of `(neighbour, km)` per node (~13 700 undirected edges).
Snapping an arbitrary lat/lon to its nearest node is O(1): `row = round((lat − lat_min)/Δ)`, `node = row·N + col`.
Chargers closer than one lattice cell (~1.1 km) are "on site" and use the straight-line distance, so a
vehicle parked at its depot is not routed round the block.

**Algorithm.** Binary-heap Dijkstra from the snapped origin, with two early exits: stop when the popped
distance exceeds `range_km` (everything still in the heap is farther), and stop when *k* target nodes have
been settled. Settled order is non-decreasing distance, so the first *k* hits are the answer. Predecessor
pointers give the polyline returned to the UI.

```
nearest_reachable(G, origin, chargers, range_km, k):
    targets ← {snap(c) → [c…]}          # chargers grouped by lattice node   O(C)
    dist[·] ← ∞; dist[s] ← 0; heap ← [(0, s)]; found ← []
    while heap and |found| < k:
        (d, u) ← pop-min(heap)
        if d > dist[u]: continue         # stale entry
        if d > range_km: break           # early exit: nothing closer remains
        if u ∈ targets: found += targets.pop(u) with distance d
        for (v, w) in adj[u]:
            if d + w ≤ range_km and d + w < dist[v]:
                dist[v] ← d + w; prev[v] ← u; push(heap, (d + w, v))
    return found[:k] with paths from prev
```

**Complexity.** Let V', E' be the nodes/edges inside the reachable region. Time O((V' + E') log V') with a
binary heap (stale entries are skipped instead of decrease-key); space O(V) for `dist`/`prev` plus O(C) for
the targets map. Building the lattice is O(V + E) once per city. `reachable_set` (the isochrone used for
STRAND_RISK) is the same loop without targets.

---

## 2. Cost-optimal charging schedule — dynamic programming

**Problem.** A vehicle arrives at a charger at `t_arr` with `soc_now` and must leave at `t_dep` with
`soc ≥ target`. Electricity price follows a time-of-use tariff (seed: off-peak ₹6.5 22:00–06:00, normal ₹9.0,
peak ₹12.5 18:00–22:00, per day of week in the tariff's timezone). Choose *when* and *how hard* to charge so
the bill is minimal; optionally charge a shadow price for calendar ageing (kWh above 80 %) and cycle ageing
(DC fast charging).

**Model.** Time is discretised into 15-min slots `t = 0…T−1` (T = 96 for 24 h) and SoC into 1 % buckets
`s = 0…100` (S = 101). In each slot the vehicle charges at a power level `p ∈ {0, 50 %, 100 %}` of the usable
kW (min of charger and vehicle AC/DC limit), which moves the bucket by `Δp = round(kW·p·0.25 h / kWh_batt × 100)`
and clamps at 100. Energy is accounted from the realised bucket delta so slot kWh, SoC and cost are
consistent. Cost of a transition `s → s'` in slot `t`:

`price[t] · kWh + λ80 · kWh_above_80 + λdc · kWh   (λ terms default to 0)`

```
optimise(req):
    price[0..T) ← slot_prices(arrival, tariff, tz)           # local dow/minute lookup, O(T·k)
    cost[s] ← ∞ for all s; cost[s0] ← 0
    for t in 0..T−1:
        new ← cost                                          # p = 0: idle, free
        for each charging level with bucket delta Δ > 0:
            for s in 0..100−Δ:      s' = s + Δ               # vectorised over s (numpy)
                c = cost[s] + trans(s, s', price[t]); if c < new[s']: new[s'] ← c; back[t][s'] ← (s, p)
            for s in 101−Δ..100:    s' = 100                 # clamped states: keep the cheapest one
        cost ← new
    feasible ← ∃ s ≥ target with cost[s] < ∞
    best ← argmin_{s ≥ target} cost[s]   if feasible   else   max{s : cost[s] < ∞}
    backtrack back[·] from (T, best) → kW per slot → merge contiguous runs into Slot rows
    baseline ← simulate "charge at max power from arrival until target" over the same prices
```

**Infeasibility.** If the target cannot be reached before departure the DP returns the plan reaching the
highest attainable SoC (that state is reachable by exactly one policy: charge at max in every slot) and sets
`feasible = False`; the use case still writes the plan (status READY) with a reason string so the API can
show "reaches 64 % by 06:00".

**Baseline and savings.** `baseline_cost` is the bill of the naive policy (plug in and charge at max power
until the target), which is itself a policy inside the DP's search space, so `baseline ≥ optimal` always and
`savings = baseline − total_cost` is a measurable, honest number. With the seed tariff a 17:00 arrival /
06:00 departure plan for a 60 kWh van (30 → 80 %) costs ₹195 instead of ₹329 (−41 %).

**Complexity.** Time O(T × S × P): 96 × 101 × 3 ≈ 29 K transitions, done as 96 × 2 numpy vector ops of
length ≤ 101. Space O(T × S) for the two back-pointer arrays (`int16` + `int8`, ~29 KB) and O(S) for the
rolling cost vector. Backtracking and run merging are O(T).

---

## 3. Depot capacity smoothing — greedy slack-first post-pass (heuristic)

**Problem.** Each vehicle's DP schedule is individually optimal, so at one depot they all pile into the
same cheapest slots and the sum of kW can exceed `depot.grid_capacity_kw` (e.g. 36 vans × 22 kW = 792 kW on a
600 kW connection). The exact problem — many vehicles, each with an arrival/departure window, a per-vehicle
power cap, an energy demand and time-varying prices, sharing a capacity per slot — generalises multi-machine
scheduling with release dates and deadlines / bin packing and is NP-hard, so we use a documented heuristic
rather than an ILP (an ILP is the obvious upgrade if a solver is allowed).

**Algorithm.** Project every schedule onto the depot's common 15-min horizon (`VehicleLoad`: window
`[offset, depart)`, `max_kw`, `kw[t]`, `price[t]`). Walk the slots; for each overloaded slot take the
vehicles charging in it ordered by *slack* — latest departure first, then most spare slots — and move their
kW to the *next-cheapest feasible* slot in their own window: one where both the vehicle's own `max_kw` and the
depot's remaining capacity have room (partial moves allowed). Repeat passes until every slot is within
capacity or a full pass makes no move (then `feasible = False`, reported per depot).

```
smooth_depot(loads, cap, T):
    load[t] ← Σ_v kw_v[t]
    repeat ≤ max_passes:
        moved ← false
        for t in 0..T−1 while load[t] > cap:
            for v in vehicles charging at t, sorted by (−depart_v, −free_slots_v):
                amount ← min(kw_v[t], load[t] − cap)
                for t' in window(v) \ {t}, sorted by (price_v[t'], |t' − t|):
                    q ← min(amount, max_kw_v − kw_v[t'], cap − load[t'])
                    if q > 0: move q kW from t to t'; amount −= q; moved ← true
        if max(load) ≤ cap or not moved: break
```

**Invariants.** Per-vehicle energy is conserved, so final SoC and the departure target are untouched and
SoC can never exceed 100 %; charge is only ever placed inside the vehicle's own window and below its own
`max_kw`; a move never creates a new overload because it only targets slots with spare depot capacity.

**Complexity.** Worst case O(passes × T × V × T log T) (sorting a window per move); in practice only
overloaded slots and the few vehicles with slack are touched, and the nightly run of a whole depot completes
in a few milliseconds. Space O(V × T).

---

## 4. Trip / stop segmentation — single-pass change-point detector

**Problem.** Split a vehicle's raw (GPS, speed, SoC) sequence into trips and stops despite GPS jitter
(a parked vehicle "moves" ±5–20 m), momentary zero speed at traffic lights, and missing SoC.

**Algorithm.** A two-state online detector — the O(n) special case of the change-point DP where the only
state carried forward is (phase, stop anchor, phase start). A stop *candidate* begins at the first sample with
`speed < 2 km/h`; it survives while every following sample is `< 2 km/h` **and** within 50 m of the anchor
sample; it becomes a confirmed *stop* once it has lasted ≥ 180 s (then the running trip is closed at the
anchor). A candidate broken before 180 s (traffic light, jitter) is folded back into the trip, including its
distance. Trip distance is Σ haversine between consecutive samples; energy is `ΔSoC × battery_kWh`, clamped
at 0 (regen); stop location is the centroid of its samples.

```
segment(points):
    for each point p (in ts order):
        if no candidate:  trip_km += step; if speed < 2: candidate ← p
        elif speed < 2 and dist(anchor, p) < 50 m:
            if not confirmed and p.ts − anchor.ts ≥ 180 s: confirmed ← true; close trip at anchor
        else:
            if confirmed: emit Stop(anchor.ts, prev.ts, centroid); new trip starts at prev
            else:         trip_km += candidate_km + step        # jitter: stays inside the trip
            candidate ← p if speed < 2 else none
    flush: confirmed candidate → Stop, otherwise close the trip at the last sample
```

**Complexity.** O(n) time, one haversine per sample; O(1) extra state (output aside).

---

## 5. VIN validation — regex + ISO 3779 check digit  (`orbit_common/vin.py`)

`VIN_RE = ^[A-HJ-NPR-Z0-9]{17}$` rejects I, O and Q and wrong lengths. The check digit (position 9) is
computed with the ISO 3779 transliteration (A=1 … Z=9, digits as themselves) and weights
`[8,7,6,5,4,3,2,10,0,9,8,7,6,5,4,3,2]`: `r = Σ value(c_i)·w_i mod 11`, digit = `'X'` if `r = 10` else `str(r)`.
`is_valid` = regex match ∧ `check_digit(vin) == vin[8]`; `make_valid` rewrites position 9 (used by the seed and
simulator so 100 K VINs are valid and deterministic). Time O(17) = O(1), space O(1). Every canonical event runs
this in the `CanonicalEvent.vin` validator.

## 6. DTC parsing — regex + lookup table  (`orbit_common/dtc.py`)

`DTC_RE = \b([PCBU])([0-3])([0-9A-F]{3})\b`: system letter (Powertrain/Chassis/Body/Network), a generic
(0, 2) vs manufacturer (1, 3) digit, three hex characters. `parse(code)` uses `fullmatch` and returns a frozen
`Dtc(code, system, generic, description)`; `is_battery` is an O(1) dictionary probe into `BATTERY_CODES`
(P0A80, P0A7F, P0AFA, P0B3D, P1A10, P0D2A) that drives the `BATTERY_DTC` alert. `extract_all(raw)` is an
O(n) `finditer` scan over free-form OEM strings such as `"P0301;P0A80,"`.

## 7. Dedupe at 100 K events/s — rolling Bloom filter  (`orbit_common/streaming.py`)

`BloomFilter(capacity n, fp_rate p)` sizes itself with the textbook formulas `m = −n·ln p / (ln 2)²` bits and
`k = round(m/n · ln 2)` hashes, stores bits in a `numpy.uint8` array, and derives the k positions by double
hashing on one 128-bit murmur3: `pos_i = (h1 + i·h2) mod m`. `add` returns True when all k bits were already
set (probable duplicate). `RollingBloom` keeps two generations (`current`, `previous`) and rotates when
`window_s` (default 600 s) elapses **or** `current` reaches capacity, so memory is bounded and old
`(vin, seq)` keys age out; `seen(key)` = `key ∈ previous` ∨ `current.add(key)`. Per op O(k) (k = 10 for
p = 0.1 %); memory 2·m bits — for 1 M keys per window at 0.1 % that is 2 × 1.8 MB per partition. No false
negatives; false positives (a fresh event dropped) are bounded by p and the unique index `(vin, seq)` on replay
is the second line of defence.

## 8. Top-K DTC frequency — Count-Min sketch  (`orbit_common/streaming.py`)

`CountMinSketch(width w = 2048, depth d = 4, k = 20)`: a `d × w` `int64` table; `add(key, n)` increments
`table[j][mmh3(key, seed=j) mod w]` for each row j and returns the minimum of the d touched counters (the
estimate, which never under-counts). `estimate` re-hashes without writing. A small dict of candidate counts is
kept alongside and pruned to the 2k largest whenever it exceeds 4k entries; `top()` returns the k most common.
Update and query O(d); space O(w × d) = 8 192 counters (64 KB) regardless of the number of distinct codes;
over-estimate ≤ ε·N with ε = e/w ≈ 0.13 % at probability 1 − e^−d ≈ 98 %.

## 9. Charger / vehicle proximity — geohash + Redis GEO  (`orbit_common/geo.py`)

`encode(lat, lon, precision=6)` interleaves longitude and latitude bisection bits and emits base-32
characters; precision 6 is a ≈1.2 km × 0.6 km cell, used as a prefix key (trip start/end cells, cluster
adjacency). O(precision) = O(1). Live proximity uses Redis GEO (`geo:chargers`, `geo:vehicles:{tenant}`), a
sorted set keyed by the 52-bit geohash integer: `GEOSEARCH … BYRADIUS r km` is O(N + log M) for N results in
the bounding cells, and the planner then pipelines `HGETALL charger:{id}`. `haversine_km` (great-circle
distance, R = 6 371.0088 km) is the metric behind the road-graph weights, snapping and trip distances.

---

## Scale tested

Laptop, single core, Python 3.12, numpy 2.x. `cd services && uv run python planner/bench.py --vehicles 10000`.

| Algorithm | Input | Measured | Notes |
|---|---|---|---|
| Road graph build | 60×60 lattice (3 600 nodes, ~13 700 edges, 2 rivers) | 6.0 ms | once per city, then LRU-cached |
| Dijkstra, early exit | 40 chargers, range 60 km, k = 5, random origins | median 0.20 ms, p95 0.34 ms | stops after 5 settled targets |
| Dijkstra, full | whole 3 600-node graph, no cut-off | median 1.80 ms, p95 2.08 ms | upper bound (isochrone for STRAND_RISK) |
| DP schedule | 96 slots × 101 SoC × 3 powers, seed tariff | median 2.27 ms, p95 2.41 ms | 50.5 % cheaper than charge-on-arrival |
| Depot smoothing | 300 vehicles × 40 slots, capacity = 20 % of Σ max kW | 1.0 ms, peak 1606 → 1320 kW (cap 1320), 45 moves, feasible | |
| Trip segmentation | 100 000 GPS samples (≈ 11.5 days at 10 s) | 41.0 ms → 556 trips, 559 stops | O(n) |
| End-to-end plan (use case) | 10 000 vehicles, sequential, in-memory ports | 14.9 s total, 1.49 ms per vehicle (9,993 READY, 7 FAILED as infeasible) | Dijkstra + DP + charger choice per vehicle, no DB/Kafka |
| Bloom filter (common tests) | 50 000 keys at p = 0.1 % | FP rate < 0.5 % measured, 0 false negatives | `common/tests/test_streaming.py` |

The end-to-end number means the nightly job for the full 100 K-vehicle simulation (85 % BEV/PHEV) is
≈ 15 minutes single-threaded, or under two minutes on 8 cores by tenant; the streaming path
(`plans.requested → plans.ready`) is dominated by the Postgres write, not the algorithms.

### Finding from the benchmark

The first 10,000-vehicle run did not finish in 15 minutes. Cost per plan grew with every plan, which points to
accumulated state, not algorithm cost. The cause was `candidates += public_chargers`: the use case extended
the list object returned by the repository, so every low-SoC plan permanently added chargers to the depot's
own list. Dijkstra then reconstructed a path for every settled charger, which made it quadratic. Two fixes:
copy the list before extending it (regression test `test_candidate_list_from_repository_is_not_mutated`),
and rank candidates first so paths are rebuilt only for the k winners. After both, time per plan is flat at
about 1.5 ms from 100 to 10,000 vehicles.
