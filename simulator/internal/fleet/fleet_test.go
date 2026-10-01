package fleet

import (
	"math/rand/v2"
	"testing"

	"github.com/orbit-fleet/simulator/internal/oem"
	"github.com/orbit-fleet/simulator/internal/road"
)

func newBEV(rng *rand.Rand) *Vehicle {
	v := &Vehicle{TenantID: 1, OEM: oem.A, Model: Model{Powertrain: BEV, CapacityKwh: 60, RangeKm: 320, MaxAcKw: 11, MaxDcKw: 100},
		Depot: road.Pos{Lat: 13.0827, Lon: 80.2707}}
	copy(v.VIN[:], "1HGCM82633A004352")
	v.Init(rng, 0)
	return v
}

func TestSocBoundsAndSeqMonotonic(t *testing.T) {
	rng := rand.New(rand.NewPCG(3, 3))
	var e oem.Event
	for n := 0; n < 20; n++ {
		v := newBEV(rng)
		var lastSeq uint64
		states := map[State]int{}
		for i := int64(1); i <= 3*86400; i += 5 { // three simulated days at 5 s steps
			hour := float64(i%86400) / 3600
			v.Step(i*1000, hour, rng, &e)
			if e.Seq != lastSeq+1 {
				t.Fatalf("seq not monotonic: %d after %d", e.Seq, lastSeq)
			}
			lastSeq = e.Seq
			if e.Soc < 0 || e.Soc > 100 || e.Soh < 0 || e.Soh > 100 {
				t.Fatalf("soc/soh out of range: %v %v", e.Soc, e.Soh)
			}
			if e.SpeedKmh < 0 || e.SpeedKmh > road.MaxSpeedKmh {
				t.Fatalf("speed out of range %v", e.SpeedKmh)
			}
			if !v.Bounds.Contains(road.Pos{Lat: e.Lat, Lon: e.Lon}) && road.DistanceKm(road.Pos{Lat: e.Lat, Lon: e.Lon}, v.Depot) > 60 {
				t.Fatalf("vehicle far outside bounds: %v %v", e.Lat, e.Lon)
			}
			states[v.State]++
		}
		if states[Trip] == 0 || states[Charging] == 0 {
			t.Fatalf("vehicle %d never tripped/charged: %v", n, states)
		}
	}
}

func TestTripDrainsSoc(t *testing.T) {
	rng := rand.New(rand.NewPCG(9, 9))
	v := newBEV(rng)
	v.Soc = 90
	v.State = Trip
	v.untilMs = 1 << 40
	road.Start(&v.Motion, rng)
	var e oem.Event
	start := v.Soc
	var first oem.Evt
	for i := int64(1); i <= 1800; i++ {
		v.Step(i*1000, 12, rng, &e)
		if i == 1 {
			first = e.Evt
		}
	}
	if v.Soc >= start {
		t.Fatalf("soc did not drain on trip: %v -> %v", start, v.Soc)
	}
	if v.OdoKm == 0 {
		t.Fatal("odometer did not advance")
	}
	_ = first
}

func TestChargingRaisesSocAndStops(t *testing.T) {
	rng := rand.New(rand.NewPCG(5, 5))
	v := newBEV(rng)
	v.Soc = 10
	v.atDepot = false
	v.State = Parked
	v.untilMs = 0
	var e oem.Event
	v.Step(1000, 12, rng, &e)
	if e.Evt != oem.EvtChargeStart || e.ChargeSt != oem.ChargeDC || e.ChargeKw != 100 {
		t.Fatalf("expected DC charge start, got evt=%v st=%v kw=%v", e.Evt, e.ChargeSt, e.ChargeKw)
	}
	stopped := false
	for i := int64(2); i <= 7200; i++ {
		v.Step(i*1000, 12, rng, &e)
		if e.Evt == oem.EvtChargeStop {
			stopped = true
			if e.Soc < 79.9 || e.Soc > 100 {
				t.Fatalf("DC session should stop near 80%%: %v", e.Soc)
			}
			break
		}
	}
	if !stopped {
		t.Fatal("DC session never stopped")
	}
}

func TestICESendsFuelNotSoc(t *testing.T) {
	rng := rand.New(rand.NewPCG(1, 1))
	v := &Vehicle{TenantID: 2, OEM: oem.B, Model: Model{Powertrain: ICE}, Depot: road.Pos{Lat: 19.07, Lon: 72.87}}
	copy(v.VIN[:], "1HGCM82633A004352")
	v.Init(rng, 0)
	v.Soc = 50
	v.State = Trip
	v.untilMs = 1 << 40
	road.Start(&v.Motion, rng)
	var e oem.Event
	fuel0 := v.Fuel
	for i := int64(1); i <= 600; i++ {
		v.Step(i*1000, 12, rng, &e)
		if e.HasSoc || !e.HasFuel || e.ChargeSt != oem.ChargeNone {
			t.Fatalf("ICE event must carry fuel only: %+v", e)
		}
	}
	if v.Fuel >= fuel0 {
		t.Fatal("ICE fuel did not drain")
	}
}

func TestFastDegradeCohort(t *testing.T) {
	rng := rand.New(rand.NewPCG(2, 2))
	slow, fast := newBEV(rng), newBEV(rng)
	fast.FastDegrade = true
	for _, v := range []*Vehicle{slow, fast} {
		v.Soh, v.Soc, v.State, v.ChargeSt, v.ChargeKw, v.chargeTarget = 95, 5, Charging, oem.ChargeAC, 11, 100
	}
	var e oem.Event
	for i := int64(1); i <= 6*3600; i++ {
		slow.Step(i*1000, 1, rng, &e)
		fast.Step(i*1000, 1, rng, &e)
	}
	if !(fast.Soh < slow.Soh) || slow.Soh >= 95 {
		t.Fatalf("soh should degrade, fast faster: slow=%v fast=%v", slow.Soh, fast.Soh)
	}
}
