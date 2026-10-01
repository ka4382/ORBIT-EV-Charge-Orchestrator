from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from orbit_common.geo import haversine_km
from orbit_planner.domain.trips import GpsPoint, segment

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def track() -> list[GpsPoint]:
    """Synthetic: drive east 10 min, stop 5 min (jittery GPS), drive north 10 min, a 60 s traffic light,
    drive 5 min, then a final 4 min stop. 10 s sampling, SoC falling while moving."""
    pts: list[GpsPoint] = []
    lat, lon, soc, t = 13.0, 80.0, 80.0, 0

    def add(speed: float, dlat: float, dlon: float, dsoc: float = 0.0) -> None:
        nonlocal lat, lon, soc, t
        lat, lon, soc = lat + dlat, lon + dlon, soc - dsoc
        pts.append(GpsPoint(T0 + timedelta(seconds=t), lat, lon, speed, soc))
        t += 10

    for _ in range(60):
        add(40.0, 0.0, 0.001, 0.05)  # ≈ 0.108 km per sample east
    for i in range(30):
        add(0.5, (-1) ** i * 0.00005, 0.0)  # 5-min stop, ±5 m jitter
    for _ in range(60):
        add(35.0, 0.001, 0.0, 0.05)  # north
    for _ in range(6):
        add(0.0, 0.0, 0.0)  # 60 s traffic light (below min stop)
    for _ in range(30):
        add(30.0, 0.001, 0.0, 0.04)
    for _ in range(24):
        add(0.0, 0.0, 0.0)  # 4-min stop at the end
    return pts


def test_segmentation_finds_trips_and_stops() -> None:
    seg = segment(track(), battery_kwh=60.0)
    assert len(seg.trips) == 2 and len(seg.stops) == 2
    t1, t2 = seg.trips
    assert t1.start == T0 and (t1.end - t1.start).total_seconds() == 600
    east = sum(haversine_km(13.0, 80.0 + 0.001 * i, 13.0, 80.0 + 0.001 * (i + 1)) for i in range(59))
    into_stop = haversine_km(13.0, 80.06, 13.00005, 80.06)  # the trip ends at the stop's first sample
    assert t1.km == pytest.approx(east + into_stop, abs=1e-3)
    assert t1.kwh == 0.05 * 59 / 100 * 60  # SoC drop × battery
    # trip 2: last sample of stop 1 → 60 north + 6 at the light (< 180 s, stays inside) + 30 → stop 2 start
    assert (t2.end - t2.start).total_seconds() == 97 * 10
    assert t2.km > 9.0 and t2.kwh > 0
    s1, s2 = seg.stops
    assert (s1.end - s1.start).total_seconds() == 290 and abs(s1.location[1] - 80.06) < 1e-6
    assert (s2.end - s2.start).total_seconds() == 230 and s2.end == track()[-1].ts


def test_short_pause_is_not_a_stop_and_edge_cases() -> None:
    pts = [GpsPoint(T0 + timedelta(seconds=10 * i), 13.0, 80.0 + 0.001 * i, 30.0, 50.0) for i in range(30)]
    pts += [
        GpsPoint(T0 + timedelta(seconds=300 + 10 * i), 13.0, 80.03, 0.0, 50.0) for i in range(10)
    ]  # 100 s
    pts += [
        GpsPoint(T0 + timedelta(seconds=400 + 10 * i), 13.0, 80.03 + 0.001 * i, 30.0, 49.0) for i in range(30)
    ]
    seg = segment(pts)
    assert len(seg.trips) == 1 and seg.stops == []
    assert seg.trips[0].kwh == 0.0  # no battery size → no energy estimate
    assert segment([]).trips == [] and segment([]).stops == []
    parked = [GpsPoint(T0 + timedelta(seconds=10 * i), 13.0, 80.0, 0.0, None) for i in range(40)]
    seg = segment(parked)
    assert seg.trips == [] and len(seg.stops) == 1 and seg.stops[0].location == (13.0, 80.0)


def test_stop_broken_by_drift_beyond_radius() -> None:
    pts = [GpsPoint(T0 + timedelta(seconds=10 * i), 13.0, 80.0 + 0.002 * i, 40.0, 60.0) for i in range(20)]
    # creeping at 1 km/h but drifting 60 m every sample → never a stop
    pts += [
        GpsPoint(T0 + timedelta(seconds=200 + 10 * i), 13.0, 80.04 + 0.0006 * i, 1.0, 60.0) for i in range(30)
    ]
    seg = segment(pts, battery_kwh=60)
    assert seg.stops == [] and len(seg.trips) == 1
    assert seg.trips[0].end == pts[-1].ts
