"""Trip / stop segmentation of a noisy GPS + speed sequence — single pass, O(n).

A *stop* begins at the first sample where speed < `stop_speed_kmh` and the displacement from the stop's
anchor point is < `stop_radius_m`; it is confirmed once it has lasted ≥ `min_stop_s`. Samples that break
the condition before confirmation are folded back into the running trip (they were GPS jitter or a
traffic light). This is the classic two-state online change-point detector: a DP over the sequence where
the only state carried forward is (current phase, anchor, phase start), so each sample is O(1).

Trips carry distance (Σ haversine between consecutive samples, jitter-filtered by the stop radius) and
energy (SoC drop × battery kWh, clamped at 0 because regen/charging can raise SoC mid-trip).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from orbit_common.geo import haversine_km


@dataclass(frozen=True, slots=True)
class GpsPoint:
    ts: datetime
    lat: float
    lon: float
    speed_kmh: float
    soc_pct: float | None = None


@dataclass(frozen=True, slots=True)
class Trip:
    start: datetime
    end: datetime
    km: float
    kwh: float
    start_latlon: tuple[float, float]
    end_latlon: tuple[float, float]


@dataclass(frozen=True, slots=True)
class Stop:
    start: datetime
    end: datetime
    location: tuple[float, float]  # centroid of the samples in the stop


@dataclass(frozen=True, slots=True)
class Segmentation:
    trips: list[Trip]
    stops: list[Stop]


def segment(
    points: Sequence[GpsPoint],
    battery_kwh: float = 0.0,
    *,
    min_stop_s: float = 180.0,
    stop_speed_kmh: float = 2.0,
    stop_radius_m: float = 50.0,
    min_trip_km: float = 0.05,
) -> Segmentation:
    """Split `points` (sorted by ts) into alternating trips and stops. O(n) time, O(1) extra state."""
    trips: list[Trip] = []
    stops: list[Stop] = []
    if not points:
        return Segmentation(trips, stops)

    def close_trip(start_i: int, end_i: int, km: float) -> None:
        """Trip = samples [start_i, end_i]. Skipped when it has no real movement."""
        if end_i <= start_i or km < min_trip_km:
            return
        a, b = points[start_i], points[end_i]
        soc_a = a.soc_pct if a.soc_pct is not None else 0.0
        soc_b = b.soc_pct if b.soc_pct is not None else soc_a
        kwh = max(0.0, (soc_a - soc_b) / 100.0 * battery_kwh) if battery_kwh else 0.0
        trips.append(Trip(a.ts, b.ts, round(km, 3), round(kwh, 3), (a.lat, a.lon), (b.lat, b.lon)))

    trip_start = 0
    trip_km = 0.0
    cand_start: int | None = None  # index where a candidate stop began
    cand_km = 0.0  # distance accumulated since the candidate began (jitter)
    in_stop = False
    lat_sum = lon_sum = 0.0
    n_stop = 0

    for i, p in enumerate(points):
        step_km = haversine_km(points[i - 1].lat, points[i - 1].lon, p.lat, p.lon) if i else 0.0
        if cand_start is None:
            trip_km += step_km
            if p.speed_kmh < stop_speed_kmh:
                cand_start, cand_km, lat_sum, lon_sum, n_stop = i, 0.0, p.lat, p.lon, 1
            continue
        anchor = points[cand_start]
        disp_m = haversine_km(anchor.lat, anchor.lon, p.lat, p.lon) * 1000.0
        still = p.speed_kmh < stop_speed_kmh and disp_m < stop_radius_m
        if still:
            cand_km += step_km
            lat_sum += p.lat
            lon_sum += p.lon
            n_stop += 1
            if not in_stop and (p.ts - anchor.ts).total_seconds() >= min_stop_s:
                in_stop = True
                close_trip(trip_start, cand_start, trip_km)
            continue
        # candidate broken by movement
        if in_stop:
            stops.append(Stop(anchor.ts, points[i - 1].ts, (lat_sum / n_stop, lon_sum / n_stop)))
            trip_start, trip_km = i - 1, step_km
        else:
            trip_km += cand_km + step_km  # jitter inside a trip: keep it
        cand_start, in_stop = None, False
        if p.speed_kmh < stop_speed_kmh:  # moved but slow: could be the start of a new stop
            cand_start, cand_km, lat_sum, lon_sum, n_stop = i, 0.0, p.lat, p.lon, 1

    last = len(points) - 1
    if cand_start is not None and in_stop:
        stops.append(Stop(points[cand_start].ts, points[last].ts, (lat_sum / n_stop, lon_sum / n_stop)))
    else:
        close_trip(trip_start, last, trip_km + cand_km)
    return Segmentation(trips, stops)
