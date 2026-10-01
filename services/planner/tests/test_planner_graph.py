from __future__ import annotations

from dataclasses import dataclass

import pytest
from orbit_common.geo import haversine_km
from orbit_planner.domain import graph as g


@dataclass(frozen=True)
class Pt:
    id: int
    lat: float
    lon: float


def hand_graph() -> g.RoadGraph:
    """3×3 lattice, unit spacing; node = row*3 + col; lat = row, lon = col. Edges chosen by hand:
    0-1 (1), 1-2 (1), 0-2 (5, a slow direct road), 2-5 (4), 5-8 (4), 0-3 (1), 3-6 (1), 6-7 (20)."""
    n = 3
    adj: list[list[tuple[int, float]]] = [[] for _ in range(9)]

    def edge(a: int, b: int, w: float) -> None:
        adj[a].append((b, w))
        adj[b].append((a, w))

    for a, b, w in [(0, 1, 1), (1, 2, 1), (0, 2, 5), (2, 5, 4), (5, 8, 4), (0, 3, 1), (3, 6, 1), (6, 7, 20)]:
        edge(a, b, w)
    return g.RoadGraph(
        n=n,
        lat_min=0,
        lon_min=0,
        dlat=1,
        dlon=1,
        lat=[i // n for i in range(9)],
        lon=[i % n for i in range(9)],
        adj=adj,
    )


def test_dijkstra_returns_true_nearest_by_road_not_by_edge_count() -> None:
    gr = hand_graph()
    chargers = [Pt(1, 2.0, 2.0), Pt(2, 0.0, 2.0), Pt(3, 2.0, 1.0)]  # nodes 8 (10 km), 2 (2 km), 7 (22 km)
    res = g.nearest_reachable(gr, (0.0, 0.0), chargers, range_km=100, k=3, local_km=0)
    assert [r.charger.id for r in res] == [2, 1, 3]
    assert [r.distance_km for r in res] == [2.0, 10.0, 22.0]
    assert res[0].path == ((0.0, 0.0), (0.0, 1.0), (0.0, 2.0))  # via 0-1-2, not the 5 km direct road


def test_dijkstra_respects_range_cutoff_and_k() -> None:
    gr = hand_graph()
    chargers = [Pt(1, 2.0, 2.0), Pt(2, 0.0, 2.0)]
    assert [r.charger.id for r in g.nearest_reachable(gr, (0, 0), chargers, range_km=2.0, local_km=0)] == [2]
    assert g.nearest_reachable(gr, (0, 0), chargers, range_km=1.9, local_km=0) == []
    assert [
        r.charger.id for r in g.nearest_reachable(gr, (0, 0), chargers, range_km=50, k=1, local_km=0)
    ] == [2]
    assert g.nearest_reachable(gr, (0, 0), chargers, range_km=50, k=0, local_km=0) == []
    assert g.nearest_reachable(gr, (0, 0), [], range_km=50, local_km=0) == []
    assert g.any_reachable(gr, (0, 0), chargers, 2.0) and not g.any_reachable(gr, (0, 0), chargers, 1.0)
    # on-site chargers (within one cell) are returned at straight-line distance without a lattice hop
    assert g.nearest_reachable(gr, (0, 0), [Pt(9, 0.0, 0.5)], range_km=100)[0].distance_km == pytest.approx(
        55.6, abs=0.2
    )
    assert g.nearest_reachable(gr, (0, 0), [Pt(9, 0.0, 0.5)], range_km=1) == []


def test_reachable_set_is_pruned_by_range() -> None:
    gr = hand_graph()
    within = g.reachable_set(gr, (0, 0), 2.0)
    assert within == {0: 0.0, 1: 1.0, 2: 2.0, 3: 1.0, 6: 2.0}
    assert 4 not in g.reachable_set(gr, (0, 0), 1000)  # node 4 is isolated


def test_snap_is_nearest_node_and_clamps_outside_box() -> None:
    gr = hand_graph()
    assert gr.snap(0.4, 0.4) == 0 and gr.snap(0.6, 0.6) == 4 and gr.snap(1.2, 2.3) == 5
    assert gr.snap(-5, 99) == 2 and gr.snap(99, -5) == 6
    assert gr.node_latlon(5) == (1, 2)


def test_build_graph_lattice_shape_rivers_and_determinism() -> None:
    a = g.build_graph(13.08, 80.27, n=20, box_deg=0.2)
    b = g.build_graph(13.08, 80.27, n=20, box_deg=0.2)
    assert a.node_count == 400 and a.edge_count == b.edge_count
    assert a.adj == b.adj  # seeded from the centre → reproducible
    assert a.removed_edges > 0  # rivers removed some crossings
    full = 2 * 19 * 20 + 2 * 19 * 19  # orthogonal + diagonal edges of a 20×20 king graph
    assert a.edge_count == full - a.removed_edges
    # weights are haversine × factor in [1, 1.6]
    for u in range(a.node_count):
        for v, w in a.adj[u]:
            d = haversine_km(a.lat[u], a.lon[u], a.lat[v], a.lon[v])
            assert d * 1.0 - 1e-9 <= w <= d * 1.6 + 1e-9
    # every node still reachable (bridges keep the lattice connected)
    assert len(g.reachable_set(a, (13.08, 80.27), 1e9)) == 400
    with pytest.raises(ValueError):
        g.build_graph(0, 0, n=1)


def test_graph_for_city_is_cached_per_city() -> None:
    g1 = g.graph_for_city(13.0827, 80.2707, n=10, box_deg=0.3)
    g2 = g.graph_for_city(13.0801, 80.2699, n=10, box_deg=0.3)  # same city after rounding to 0.01°
    g3 = g.graph_for_city(12.9716, 77.5946, n=10, box_deg=0.3)
    assert g1 is g2 and g1 is not g3


def test_default_city_graph_finds_chargers_across_a_river() -> None:
    gr = g.graph_for_city(13.08, 80.27)
    assert gr.node_count == 3600
    chargers = [Pt(i, 13.08 + 0.01 * i, 80.27 - 0.02 * i) for i in range(1, 6)]
    res = g.nearest_reachable(gr, (13.08, 80.27), chargers, range_km=100, k=5)
    assert [r.charger.id for r in res] == [1, 2, 3, 4, 5]
    assert all(res[i].distance_km <= res[i + 1].distance_km for i in range(4))
    assert res[0].distance_km >= 1.0  # road distance ≥ straight line (factor ≥ 1)
    assert 0.9 < gr.cell_km < 1.2  # 0.6° / 59 ≈ 1.13 km cells
