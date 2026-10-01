"""Synthetic road graph per city and Dijkstra with early exit ("nearest reachable chargers under range").

The road network is a lattice of N×N nodes over a `box_deg`×`box_deg` box centred on the city.
Every node has 8-neighbour edges (king moves) whose weight is the haversine distance in km multiplied by
a seeded random "road factor" in [1.0, 1.6] (detours, congestion). A few "rivers" cut the lattice and
are only crossable at bridges, so road distance genuinely differs from straight-line distance.

Snapping an arbitrary lat/lon to the nearest node is O(1) (integer division on the grid index).
Graphs are cached per city in a module-level LRU so the cost of building (O(V + E)) is paid once.
"""

from __future__ import annotations

import heapq
import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from orbit_common.geo import haversine_km

_NEIGHBOURS: tuple[tuple[int, int], ...] = ((0, 1), (1, -1), (1, 0), (1, 1))  # each undirected edge once


class ChargerPoint(Protocol):
    """Anything with an id and a position; the planner passes `models.Charger`."""

    @property
    def id(self) -> int: ...
    @property
    def lat(self) -> float: ...
    @property
    def lon(self) -> float: ...


@dataclass(slots=True)
class RoadGraph:
    """Lattice road graph. Node index = row * n + col; adjacency = list of (neighbour, km) per node."""

    n: int
    lat_min: float
    lon_min: float
    dlat: float
    dlon: float
    lat: list[float]
    lon: list[float]
    adj: list[list[tuple[int, float]]]
    removed_edges: int = 0

    @property
    def node_count(self) -> int:
        return self.n * self.n

    @property
    def edge_count(self) -> int:
        return sum(len(a) for a in self.adj) // 2

    def snap(self, lat: float, lon: float) -> int:
        """Nearest lattice node to (lat, lon). O(1). Points outside the box clamp to the border."""
        r = min(self.n - 1, max(0, int(round((lat - self.lat_min) / self.dlat))))
        c = min(self.n - 1, max(0, int(round((lon - self.lon_min) / self.dlon))))
        return r * self.n + c

    def node_latlon(self, node: int) -> tuple[float, float]:
        return self.lat[node], self.lon[node]

    @property
    def cell_km(self) -> float:
        """Lattice spacing in km (the position quantisation error of `snap`)."""
        return haversine_km(self.lat_min, self.lon_min, self.lat_min + self.dlat, self.lon_min)


def build_graph(
    center_lat: float,
    center_lon: float,
    n: int = 60,
    box_deg: float = 0.6,
    seed: int | None = None,
    rivers: int = 2,
    bridge_every: int = 12,
) -> RoadGraph:
    """Build the synthetic lattice. Time O(V + E) = O(n²); space O(V + E).

    Deterministic for a given (centre, n, box, seed): the default seed is derived from the centre so the
    same city always yields the same graph across processes (Dijkstra results are reproducible).
    """
    if n < 2:
        raise ValueError("n must be >= 2")
    if seed is None:
        seed = int(round(center_lat * 1000)) * 100_003 + int(round(center_lon * 1000))
    rng = random.Random(seed)
    lat_min, lon_min = center_lat - box_deg / 2, center_lon - box_deg / 2
    dlat = dlon = box_deg / (n - 1)
    lat = [lat_min + (i // n) * dlat for i in range(n * n)]
    lon = [lon_min + (i % n) * dlon for i in range(n * n)]
    adj: list[list[tuple[int, float]]] = [[] for _ in range(n * n)]

    # Rivers: a horizontal cut between row r and r+1, crossable only by straight bridges every `bridge_every`.
    river_rows: dict[int, set[int]] = {}
    for _ in range(rivers):
        row = rng.randrange(n // 4, 3 * n // 4)
        offset = rng.randrange(bridge_every)
        river_rows[row] = {c for c in range(n) if (c + offset) % bridge_every == 0}

    removed = 0
    for r in range(n):
        for c in range(n):
            u = r * n + c
            for dr, dc in _NEIGHBOURS:
                r2, c2 = r + dr, c + dc
                if not (0 <= r2 < n and 0 <= c2 < n):
                    continue
                if dr == 1 and r in river_rows and not (dc == 0 and c in river_rows[r]):
                    removed += 1
                    continue
                v = r2 * n + c2
                w = haversine_km(lat[u], lon[u], lat[v], lon[v]) * rng.uniform(1.0, 1.6)
                adj[u].append((v, w))
                adj[v].append((u, w))
    return RoadGraph(
        n=n,
        lat_min=lat_min,
        lon_min=lon_min,
        dlat=dlat,
        dlon=dlon,
        lat=lat,
        lon=lon,
        adj=adj,
        removed_edges=removed,
    )


@lru_cache(maxsize=32)
def _cached_graph(lat_r: float, lon_r: float, n: int, box_deg: float) -> RoadGraph:
    return build_graph(lat_r, lon_r, n=n, box_deg=box_deg)


def graph_for_city(lat: float, lon: float, n: int = 60, box_deg: float = 0.6) -> RoadGraph:
    """Graph for the city containing (lat, lon), built on first use and cached (LRU of 32 cities).

    The centre is rounded to 0.01° so all depots of a city share one graph.
    """
    return _cached_graph(round(lat, 2), round(lon, 2), n, box_deg)


@dataclass(frozen=True, slots=True)
class ReachableCharger:
    charger: ChargerPoint
    distance_km: float
    path: tuple[tuple[float, float], ...]  # lat/lon polyline from origin node to charger node


def _dijkstra(
    graph: RoadGraph, src: int, range_km: float, targets: dict[int, list[ChargerPoint]] | None, k: int
) -> tuple[list[float], list[int], list[tuple[ChargerPoint, float]]]:
    """Binary-heap Dijkstra from `src`, pruned at `range_km`. Returns (dist, prev, found targets in order).

    Time O((V' + E') log V') where V', E' are the nodes/edges within `range_km` (early exit);
    space O(V) for the dist/prev arrays.
    """
    inf = math.inf
    dist = [inf] * graph.node_count
    prev = [-1] * graph.node_count
    dist[src] = 0.0
    heap: list[tuple[float, int]] = [(0.0, src)]
    found: list[tuple[ChargerPoint, float]] = []
    adj = graph.adj
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue  # stale entry
        if d > range_km:
            break  # early exit: everything left is farther than the range
        if targets is not None and u in targets:
            found.extend((ch, d) for ch in targets.pop(u))
            if len(found) >= k:
                break
        for v, w in adj[u]:
            nd = d + w
            if nd <= range_km and nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))
    return dist, prev, found


def _path(graph: RoadGraph, prev: list[int], node: int) -> tuple[tuple[float, float], ...]:
    out: list[tuple[float, float]] = []
    while node != -1:
        out.append(graph.node_latlon(node))
        node = prev[node]
    out.reverse()
    return tuple(out)


def nearest_reachable(
    graph: RoadGraph,
    origin: tuple[float, float],
    chargers: Iterable[ChargerPoint],
    range_km: float,
    k: int = 5,
    local_km: float | None = None,
) -> list[ReachableCharger]:
    """The k chargers with the smallest *road* distance from `origin` that are within `range_km`.

    Chargers closer than `local_km` (default: one lattice cell) are "on site": the lattice cannot resolve
    sub-cell positions, so their distance is the straight-line distance and no hop is charged for them
    (a vehicle parked at its depot must not be routed round the block). Everything else goes through
    Dijkstra, which stops expanding as soon as the popped distance exceeds `range_km` or k chargers are
    settled, so the work is bounded by the reachable region, not the whole city. Ties resolve in charger
    order. Time O((V' + E') log V' + C) for C candidate chargers; space O(V + C).
    """
    if k <= 0 or range_km < 0:
        return []
    if local_km is None:
        local_km = graph.cell_km
    # Candidates are ranked first and paths are rebuilt only for the k winners: path reconstruction is
    # O(path length) and doing it for every settled charger made fleet planning quadratic.
    local: list[tuple[float, int, ChargerPoint]] = []
    targets: dict[int, list[ChargerPoint]] = {}
    order: dict[int, int] = {}
    for i, ch in enumerate(chargers):
        order[id(ch)] = i
        straight = haversine_km(origin[0], origin[1], ch.lat, ch.lon)
        if straight <= local_km:
            if straight <= range_km:
                local.append((straight, i, ch))
        else:
            targets.setdefault(graph.snap(ch.lat, ch.lon), []).append(ch)
    ranked: list[tuple[float, int, ChargerPoint, list[int] | None]] = [(d, i, ch, None) for d, i, ch in local]
    if targets and len(local) < k:
        src = graph.snap(*origin)
        _, prev, hits = _dijkstra(graph, src, range_km, targets, k - len(local))
        ranked.extend((d, order[id(ch)], ch, prev) for ch, d in hits)
    ranked.sort(key=lambda r: (r[0], r[1]))
    out: list[ReachableCharger] = []
    for d, _, ch, pred in ranked[:k]:
        if pred is None:
            out.append(ReachableCharger(ch, d, (origin, (ch.lat, ch.lon))))
        else:
            out.append(ReachableCharger(ch, d, _path(graph, pred, graph.snap(ch.lat, ch.lon))))
    return out


def reachable_set(graph: RoadGraph, origin: tuple[float, float], range_km: float) -> dict[int, float]:
    """All nodes reachable within `range_km` and their road distance (STRAND_RISK check, isochrones).

    Time O((V' + E') log V'), space O(V).
    """
    src = graph.snap(*origin)
    dist, _, _ = _dijkstra(graph, src, range_km, None, 0)
    return {i: d for i, d in enumerate(dist) if d <= range_km}


def any_reachable(
    graph: RoadGraph, origin: tuple[float, float], chargers: Sequence[ChargerPoint], range_km: float
) -> bool:
    """True iff at least one charger is within road range. Stops at the first hit."""
    return bool(nearest_reachable(graph, origin, chargers, range_km, k=1, local_km=0.0))
