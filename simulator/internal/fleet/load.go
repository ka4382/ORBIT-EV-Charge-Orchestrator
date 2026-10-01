package fleet

import (
	"context"
	"fmt"
	"math/rand/v2"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/orbit-fleet/simulator/internal/oem"
	"github.com/orbit-fleet/simulator/internal/road"
	"github.com/orbit-fleet/simulator/internal/vin"
)

// Cities mirrors seed.py:CITIES (name, lat, lon).
var Cities = []struct {
	Name     string
	Lat, Lon float64
}{
	{"Chennai", 13.0827, 80.2707}, {"Bengaluru", 12.9716, 77.5946}, {"Hyderabad", 17.3850, 78.4867},
	{"Mumbai", 19.0760, 72.8777}, {"Pune", 18.5204, 73.8567}, {"Delhi", 28.6139, 77.2090},
	{"Kolkata", 22.5726, 88.3639}, {"Ahmedabad", 23.0225, 72.5714},
}

// Models mirrors seed.py:MODELS.
var Models = []Model{
	{BEV, 60, 320, 11, 100},  // A-Van 60
	{BEV, 45, 300, 7.4, 50},  // A-Sedan 45
	{PHEV, 14, 60, 3.6, 0},   // A-Hybrid
	{BEV, 120, 380, 22, 150}, // B-Truck 120
	{BEV, 30, 220, 7.4, 50},  // B-Hatch 30
	{ICE, 0, 0, 0, 0},        // B-Diesel
}

// LoadQuery is the exact query used against the seeded relational core.
const LoadQuery = `SELECT v.vin, v.tenant_id, v.fleet_id, m.powertrain, m.battery_capacity_kwh, m.nominal_range_km, m.max_ac_kw, m.max_dc_kw, d.lat, d.lon, d.id FROM vehicle v JOIN vehicle_model m ON m.id=v.model_id JOIN fleet f ON f.id=v.fleet_id JOIN depot d ON d.id=f.depot_id LIMIT $1`

// OEMFor assigns the payload format: odd tenant -> OEM_A, even -> OEM_B.
func OEMFor(tenantID int32) oem.Kind {
	if tenantID%2 == 1 {
		return oem.A
	}
	return oem.B
}

// LoadFromDB reads up to limit vehicles from Postgres.
func LoadFromDB(ctx context.Context, url string, limit int) ([]Vehicle, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, url)
	if err != nil {
		return nil, fmt.Errorf("connect: %w", err)
	}
	defer conn.Close(ctx)
	rows, err := conn.Query(ctx, LoadQuery, limit)
	if err != nil {
		return nil, fmt.Errorf("query: %w", err)
	}
	defer rows.Close()
	out := make([]Vehicle, 0, limit)
	for rows.Next() {
		var (
			vinStr, pt         string
			tenant, fleet, dep int32
			cap, rng, ac, dc   *float64
			lat, lon           float64
		)
		if err := rows.Scan(&vinStr, &tenant, &fleet, &pt, &cap, &rng, &ac, &dc, &lat, &lon, &dep); err != nil {
			return nil, fmt.Errorf("scan: %w", err)
		}
		if len(vinStr) != 17 {
			continue
		}
		v := Vehicle{TenantID: tenant, FleetID: fleet, DepotID: dep, OEM: OEMFor(tenant),
			Model: Model{Powertrain: ParsePowertrain(pt), CapacityKwh: deref(cap), RangeKm: deref(rng), MaxAcKw: deref(ac), MaxDcKw: deref(dc)},
			Depot: road.Pos{Lat: lat, Lon: lon}}
		copy(v.VIN[:], vinStr)
		out = append(out, v)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("no vehicles in database")
	}
	return out, nil
}

func deref(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}

// Synthetic generates n vehicles the way seed.py does: tenants round-robin over
// the 8 cities, 3 depots per tenant, 85 % electrified models, valid VINs.
func Synthetic(n, tenants int, seed uint64) []Vehicle {
	rng := rand.New(rand.NewPCG(seed, 0x5eed))
	type depot struct {
		id  int32
		pos road.Pos
	}
	depots := make([][]depot, tenants)
	var depotID int32
	for t := 0; t < tenants; t++ {
		c := Cities[t%len(Cities)]
		for d := 0; d < 3; d++ {
			depotID++
			depots[t] = append(depots[t], depot{depotID, road.Pos{Lat: c.Lat + (rng.Float64()-0.5)*0.3, Lon: c.Lon + (rng.Float64()-0.5)*0.3}})
		}
	}
	electrified := make([]Model, 0, len(Models))
	for _, m := range Models {
		if m.Powertrain != ICE {
			electrified = append(electrified, m)
		}
	}
	out := make([]Vehicle, 0, n)
	vid := 0
	for t := 1; t <= tenants; t++ {
		per := n/tenants + boolInt(t <= n%tenants)
		for i := 0; i < per; i++ {
			vid++
			m := Models[rng.IntN(len(Models))]
			if rng.Float64() < 0.85 {
				m = electrified[rng.IntN(len(electrified))]
			}
			d := depots[t-1][rng.IntN(3)]
			v := Vehicle{TenantID: int32(t), FleetID: d.id, DepotID: d.id, OEM: OEMFor(int32(t)), Model: m, Depot: d.pos}
			copy(v.VIN[:], vin.Synthetic(vid, rng))
			out = append(out, v)
		}
	}
	return out
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
