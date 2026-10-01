"""Geohash encode/decode and haversine. Precision 6 ≈ 1.2 km × 0.6 km cells."""

import math

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(lat: float, lon: float, precision: int = 6) -> str:
    lat_i, lon_i = (-90.0, 90.0), (-180.0, 180.0)
    bits, bit, ch, even = [16, 8, 4, 2, 1], 0, 0, True
    out: list[str] = []
    while len(out) < precision:
        if even:
            mid = (lon_i[0] + lon_i[1]) / 2
            if lon > mid:
                ch |= bits[bit]
                lon_i = (mid, lon_i[1])
            else:
                lon_i = (lon_i[0], mid)
        else:
            mid = (lat_i[0] + lat_i[1]) / 2
            if lat > mid:
                ch |= bits[bit]
                lat_i = (mid, lat_i[1])
            else:
                lat_i = (lat_i[0], mid)
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_BASE32[ch])
            bit = ch = 0
    return "".join(out)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
