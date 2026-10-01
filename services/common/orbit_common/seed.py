"""Seed the relational core: tenants, OEMs, models, depots, tariffs, chargers, fleets, 100K vehicles.
Deterministic (seeded RNG) so simulator and DB agree on VINs. Run: python -m orbit_common.seed"""

from __future__ import annotations

import io
import random
from typing import Any

import psycopg

from .config import settings
from .vin import make_valid

CITIES = [  # (name, lat, lon) — Indian metros, so tariffs use INR and Asia/Kolkata
    ("Chennai", 13.0827, 80.2707),
    ("Bengaluru", 12.9716, 77.5946),
    ("Hyderabad", 17.3850, 78.4867),
    ("Mumbai", 19.0760, 72.8777),
    ("Pune", 18.5204, 73.8567),
    ("Delhi", 28.6139, 77.2090),
    ("Kolkata", 22.5726, 88.3639),
    ("Ahmedabad", 23.0225, 72.5714),
]
MODELS = [  # (oem, name, powertrain, kwh, range_km, ac_kw, dc_kw)
    ("OEM_A", "A-Van 60", "BEV", 60, 320, 11, 100),
    ("OEM_A", "A-Sedan 45", "BEV", 45, 300, 7.4, 50),
    ("OEM_A", "A-Hybrid", "PHEV", 14, 60, 3.6, 0),
    ("OEM_B", "B-Truck 120", "BEV", 120, 380, 22, 150),
    ("OEM_B", "B-Hatch 30", "BEV", 30, 220, 7.4, 50),
    ("OEM_B", "B-Diesel", "ICE", None, None, None, None),
]
VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"


def _one(cur: psycopg.Cursor) -> Any:
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("expected a row")
    return row[0]


def vin_for(i: int, rng: random.Random) -> str:
    body = (
        "".join(rng.choice(VIN_CHARS) for _ in range(8))
        + "0"
        + "".join(rng.choice(VIN_CHARS) for _ in range(2))
        + f"{i:06d}"
    )
    return make_valid(body)


def main() -> None:
    s = settings()
    n_vehicles, n_tenants = s.sim_vehicles, s.sim_tenants
    rng = random.Random(42)
    with psycopg.connect(s.database_url, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM vehicle")
        if _one(cur) >= n_vehicles:
            print("already seeded")
            return
        cur.execute(
            "TRUNCATE tenant, oem, vehicle_model, depot, fleet, vehicle, charger, tariff, tariff_slot RESTART IDENTITY CASCADE"
        )
        cur.executemany(
            "INSERT INTO oem(code,name,payload_format) VALUES (%s,%s,%s)",
            [("OEM_A", "Alpha Motors", "flat_iso"), ("OEM_B", "Beta Automotive", "nested_epoch")],
        )
        for oem, name, pt, kwh, rng_km, ac, dc in MODELS:
            cur.execute(
                "INSERT INTO vehicle_model(oem_id,name,powertrain,battery_capacity_kwh,nominal_range_km,max_ac_kw,max_dc_kw) "
                "VALUES ((SELECT id FROM oem WHERE code=%s),%s,%s,%s,%s,%s,%s)",
                (oem, name, pt, kwh, rng_km, ac, dc),
            )
        cur.execute("SELECT id, name, powertrain FROM vehicle_model ORDER BY id")
        models = cur.fetchall()
        bev_models = [m for m in models if m[2] in ("BEV", "PHEV")]

        vehicle_rows = io.StringIO()
        charger_rows: list[tuple] = []
        vid = 0
        for t in range(1, n_tenants + 1):
            city, clat, clon = CITIES[(t - 1) % len(CITIES)]
            cur.execute("INSERT INTO tenant(name) VALUES (%s) RETURNING id", (f"Fleet Co {t} ({city})",))
            tenant_id = _one(cur)
            cur.execute(
                "INSERT INTO tariff(tenant_id,name) VALUES (%s,%s) RETURNING id",
                (tenant_id, "TOU-Commercial"),
            )
            tariff_id = _one(cur)
            # Indian commercial time-of-use: off-peak 22:00–06:00 ₹6.5, normal ₹9.0, peak 18:00–22:00 ₹12.5
            for dow in range(7):
                cur.executemany(
                    "INSERT INTO tariff_slot(tariff_id,dow,start_min,end_min,price_per_kwh) VALUES (%s,%s,%s,%s,%s)",
                    [
                        (tariff_id, dow, 0, 360, 6.5),
                        (tariff_id, dow, 360, 1080, 9.0),
                        (tariff_id, dow, 1080, 1320, 12.5),
                        (tariff_id, dow, 1320, 1440, 6.5),
                    ],
                )
            depots = []
            for d in range(3):
                dlat, dlon = clat + rng.uniform(-0.15, 0.15), clon + rng.uniform(-0.15, 0.15)
                cur.execute(
                    "INSERT INTO depot(tenant_id,name,lat,lon,grid_capacity_kw,tariff_id) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (tenant_id, f"{city} Depot {d + 1}", dlat, dlon, 600 + 300 * d, tariff_id),
                )
                depot_id = _one(cur)
                depots.append(depot_id)
                for c in range(12):
                    charger_rows.append(
                        (
                            tenant_id,
                            depot_id,
                            f"D{depot_id}-C{c + 1}",
                            dlat + rng.uniform(-0.001, 0.001),
                            dlon + rng.uniform(-0.001, 0.001),
                            22 if c < 8 else 60,
                            "Type2" if c < 8 else "CCS2",
                            False,
                            None,
                        )
                    )
            for p in range(40):  # public fast chargers around the city
                charger_rows.append(
                    (
                        None,
                        None,
                        f"{city} Public {p + 1}",
                        clat + rng.uniform(-0.25, 0.25),
                        clon + rng.uniform(-0.25, 0.25),
                        rng.choice([50, 60, 120, 150]),
                        "CCS2",
                        True,
                        rng.choice([14.0, 16.0, 18.0]),
                    )
                )
            fleets = []
            for f, depot_id in enumerate(depots):
                cur.execute(
                    "INSERT INTO fleet(tenant_id,depot_id,name) VALUES (%s,%s,%s) RETURNING id",
                    (tenant_id, depot_id, f"{city} Fleet {f + 1}"),
                )
                fleets.append(_one(cur))
            per_tenant = n_vehicles // n_tenants + (1 if t <= n_vehicles % n_tenants else 0)
            for _ in range(per_tenant):
                vid += 1
                model = rng.choice(bev_models) if rng.random() < 0.85 else rng.choice(models)
                vehicle_rows.write(
                    f"{vin_for(vid, rng)}\t{tenant_id}\t{rng.choice(fleets)}\t{model[0]}\tTN{vid % 100:02d}{vid:05d}\n"
                )
        cur.executemany(
            "INSERT INTO charger(tenant_id,depot_id,name,lat,lon,max_kw,connector,is_public,price_per_kwh) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            charger_rows,
        )
        vehicle_rows.seek(0)
        with cur.copy("COPY vehicle(vin,tenant_id,fleet_id,model_id,plate) FROM STDIN") as cp:
            cp.write(vehicle_rows.read())
        conn.commit()
        print(f"seeded {vid} vehicles, {len(charger_rows)} chargers, {n_tenants} tenants")


if __name__ == "__main__":
    main()
