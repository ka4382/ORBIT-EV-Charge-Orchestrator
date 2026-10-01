// Package fleet holds the per-vehicle state machine:
//
//	PARKED -> TRIP -> PARKED -> CHARGING -> PARKED ...
//
// Trips follow shift patterns (mostly 06:00-22:00 local), drain the battery in
// proportion to distance and speed, and end by returning to the depot. Charging
// happens at the depot (AC, model max_ac_kw) or at a public fast charger (DC,
// model max_dc_kw) when SoC drops below 20%. ICE vehicles burn fuel instead.
package fleet

import (
	"math"
	"math/rand/v2"

	"github.com/orbit-fleet/simulator/internal/oem"
	"github.com/orbit-fleet/simulator/internal/road"
)

// Powertrain mirrors vehicle_model.powertrain.
type Powertrain uint8

// Powertrain kinds.
const (
	BEV Powertrain = iota
	PHEV
	HEV
	ICE
)

// ParsePowertrain maps the DB string to a Powertrain (unknown -> BEV).
func ParsePowertrain(s string) Powertrain {
	switch s {
	case "ICE":
		return ICE
	case "PHEV":
		return PHEV
	case "HEV":
		return HEV
	default:
		return BEV
	}
}

// State is the vehicle's activity state.
type State uint8

// Vehicle states.
const (
	Parked State = iota
	Trip
	Charging
	Fueling
)

// Model is the subset of vehicle_model the simulator needs.
type Model struct {
	Powertrain  Powertrain
	CapacityKwh float64 // 0 for ICE
	RangeKm     float64
	MaxAcKw     float64
	MaxDcKw     float64
}

// Vehicle is the complete mutable state of one simulated vehicle. It is owned by
// exactly one shard goroutine, so no locking is needed.
type Vehicle struct {
	VIN      [17]byte
	TenantID int32
	FleetID  int32
	DepotID  int32
	OEM      oem.Kind
	Model    Model

	Depot  road.Pos
	Bounds road.Bounds
	Motion road.Motion

	State        State
	untilMs      int64 // state deadline in sim ms
	returning    bool  // trip phase: heading home
	atDepot      bool
	Soc          float64
	Soh          float64
	Fuel         float64
	OdoKm        float64
	Seq          uint64
	lastTsMs     int64
	ambientBias  float64
	FastDegrade  bool
	ChargeSt     oem.ChargeState
	ChargeKw     float64
	chargeTarget float64
	nDtc         uint8
	dtc          [oem.MaxDTC]string
	dtcClearMs   int64
}

// DTC catalogue with relative weights; battery codes are rarer and BEV/PHEV only.
var dtcCatalogue = []struct {
	code    string
	weight  float64
	battery bool
}{
	{"P0301", 0.55, false}, // cylinder 1 misfire (also raised by range extenders)
	{"U0100", 0.30, false}, // lost comms with ECM
	{"P0AFA", 0.10, true},  // battery system voltage low
	{"P0A80", 0.05, true},  // replace battery pack
}

const (
	dtcRatePerSec   = 1.0 / (3 * 86400) // ~one code per vehicle every 3 days
	harshBrakeRate  = 0.001             // extra random harsh brakes per second on a trip
	sohNormalPerKwh = 0.0004            // % SoH lost per kWh charged
	sohFastPerKwh   = 0.008
	iceLPer100Km    = 9.0
	iceTankL        = 60.0
	maxStepSec      = 900.0
)

// Init randomises the initial state. Call once after the static fields are set.
func (v *Vehicle) Init(rng *rand.Rand, nowMs int64) {
	v.lastTsMs = nowMs
	v.OdoKm = rng.Float64() * 150_000
	v.Soc = 40 + rng.Float64()*60
	v.Fuel = 30 + rng.Float64()*70
	if v.FastDegrade {
		v.Soh = 88 + rng.Float64()*6
	} else {
		v.Soh = 92 + rng.Float64()*8
	}
	v.ambientBias = (rng.Float64() - 0.5) * 3
	v.Bounds = road.Bounds{Centre: v.Depot, RadiusDeg: road.DefaultRadiusDeg}
	if rng.Float64() < 0.7 {
		v.Motion.Pos = v.Depot
		v.atDepot = true
	} else {
		v.Motion.Pos = road.Pos{Lat: v.Depot.Lat + (rng.Float64()-0.5)*0.2, Lon: v.Depot.Lon + (rng.Float64()-0.5)*0.2}
	}
	v.State = Parked
	v.untilMs = nowMs + int64(rng.Float64()*20*60_000)
}

func (v *Vehicle) hasBattery() bool { return v.Model.Powertrain != ICE && v.Model.CapacityKwh > 0 }
func (v *Vehicle) hasFuel() bool {
	return v.Model.Powertrain == ICE || v.Model.Powertrain == PHEV || v.Model.Powertrain == HEV
}

// tripStartRate returns the per-second probability of starting a trip at the local hour.
func tripStartRate(hour float64) float64 {
	switch {
	case hour >= 6 && hour < 10: // morning shift start
		return 1.0 / 600
	case hour >= 10 && hour < 22:
		return 1.0 / 1500
	default: // night: rare
		return 1.0 / (4 * 3600)
	}
}

func ambient(hour, bias float64) float64 {
	return 26 + 6*math.Cos((hour-15)/24*2*math.Pi) + bias
}

// Step advances the vehicle to simulated time nowMs, fills e, and returns it.
// hour is the local hour-of-day for shift patterns.
func (v *Vehicle) Step(nowMs int64, hour float64, rng *rand.Rand, e *oem.Event) {
	dt := float64(nowMs-v.lastTsMs) / 1000
	if dt < 0 {
		dt = 0
	} else if dt > maxStepSec {
		dt = maxStepSec
	}
	v.lastTsMs = nowMs
	v.Seq++
	evt := oem.EvtNone

	switch v.State {
	case Parked:
		v.Motion.SpeedKmh = 0
		if nowMs >= v.untilMs {
			evt = v.decideParked(nowMs, hour, rng)
		}
	case Trip:
		if v.returning {
			road.SteerTo(&v.Motion, v.Depot, rng)
		}
		km, harsh := road.Step(&v.Motion, v.Bounds, dt, rng)
		v.OdoKm += km
		v.consume(km)
		if harsh || rng.Float64() < harshBrakeRate*dt {
			evt = oem.EvtHarshBrake
		}
		lowEnergy := (v.hasBattery() && v.Soc < 20) || (!v.hasBattery() && v.Fuel < 10)
		switch {
		case lowEnergy && !v.returning:
			// Abort the trip and look for energy nearby.
			evt = v.endTrip(nowMs, rng, false)
		case !v.returning && nowMs >= v.untilMs:
			v.returning = true
			v.untilMs = nowMs + 90*60_000 // give up and park where we are after 90 min
		case v.returning && (road.DistanceKm(v.Motion.Pos, v.Depot) < 0.3 || nowMs >= v.untilMs):
			evt = v.endTrip(nowMs, rng, road.DistanceKm(v.Motion.Pos, v.Depot) < 0.3)
		}
	case Charging:
		v.Motion.SpeedKmh = 0
		kw := v.ChargeKw
		if v.ChargeSt == oem.ChargeDC && v.Soc > 80 {
			kw *= 0.4 // taper
		}
		kwh := kw * dt / 3600
		v.Soc += kwh / v.Model.CapacityKwh * 100
		rate := sohNormalPerKwh
		if v.FastDegrade {
			rate = sohFastPerKwh
		}
		v.Soh -= kwh * rate
		if v.Soc >= v.chargeTarget {
			v.Soc = math.Min(v.chargeTarget, 100)
			v.ChargeSt, v.ChargeKw = oem.ChargeNone, 0
			v.State = Parked
			v.untilMs = nowMs + int64((5+rng.Float64()*55)*60_000)
			evt = oem.EvtChargeStop
		}
	case Fueling:
		v.Motion.SpeedKmh = 0
		if nowMs >= v.untilMs {
			v.Fuel = 100
			v.State = Parked
			v.untilMs = nowMs + int64(rng.Float64()*10*60_000)
		}
	}

	// Diagnostic trouble codes: set rarely, clear after 1-6 h.
	if v.nDtc > 0 && nowMs >= v.dtcClearMs {
		v.nDtc = 0
	}
	if v.nDtc < oem.MaxDTC && rng.Float64() < dtcRatePerSec*dt {
		if code, ok := v.pickDtc(rng); ok {
			v.dtc[v.nDtc] = code
			v.nDtc++
			v.dtcClearMs = nowMs + int64((1+rng.Float64()*5)*3600_000)
			if evt == oem.EvtNone {
				evt = oem.EvtDtcSet
			}
		}
	}

	v.Soc = clamp01(v.Soc)
	v.Soh = clamp01(v.Soh)
	v.Fuel = clamp01(v.Fuel)

	e.VIN = v.VIN
	e.TenantID = v.TenantID
	e.TsMs = nowMs
	e.Seq = v.Seq
	e.Lat = v.Motion.Pos.Lat + (rng.Float64()-0.5)*0.00004 // ~2 m GPS jitter
	e.Lon = v.Motion.Pos.Lon + (rng.Float64()-0.5)*0.00004
	e.SpeedKmh = v.Motion.SpeedKmh
	e.HasSoc = v.hasBattery()
	e.Soc, e.Soh = v.Soc, v.Soh
	e.HasFuel = v.hasFuel()
	e.FuelPct = v.Fuel
	e.OdoKm = v.OdoKm
	e.AmbientC = ambient(hour, v.ambientBias) + (rng.Float64()-0.5)*0.4
	e.NDTC = v.nDtc
	e.DTC = v.dtc
	e.Evt = evt
	e.ChargeSt = v.ChargeSt
	e.ChargeKw = v.ChargeKw
}

func (v *Vehicle) decideParked(nowMs int64, hour float64, rng *rand.Rand) oem.Evt {
	if v.hasBattery() && v.Soc < 20 {
		return v.startCharge(nowMs, rng)
	}
	if !v.hasBattery() && v.Fuel < 15 {
		v.State = Fueling
		v.untilMs = nowMs + int64((3+rng.Float64()*5)*60_000)
		return oem.EvtNone
	}
	// Overnight top-up at the depot.
	if v.hasBattery() && v.atDepot && v.Soc < 85 && (hour >= 22 || hour < 5) && rng.Float64() < 1.0/600 {
		return v.startCharge(nowMs, rng)
	}
	if rng.Float64() < tripStartRate(hour) {
		road.Start(&v.Motion, rng)
		v.State = Trip
		v.returning = false
		v.atDepot = false
		v.untilMs = nowMs + int64((10+rng.Float64()*50)*60_000)
		return oem.EvtTripStart
	}
	v.untilMs = nowMs + 1000 // re-evaluate next second
	return oem.EvtNone
}

func (v *Vehicle) endTrip(nowMs int64, rng *rand.Rand, home bool) oem.Evt {
	v.State = Parked
	v.Motion.SpeedKmh = 0
	v.Motion.TargetKmh = 0
	v.atDepot = home
	if home {
		v.Motion.Pos = v.Depot
	}
	v.untilMs = nowMs + int64((2+rng.Float64()*30)*60_000)
	return oem.EvtTripEnd
}

func (v *Vehicle) startCharge(nowMs int64, rng *rand.Rand) oem.Evt {
	v.State = Charging
	if v.atDepot || v.Model.MaxDcKw <= 0 {
		v.ChargeSt = oem.ChargeAC
		v.ChargeKw = v.Model.MaxAcKw
		v.chargeTarget = 95 + rng.Float64()*5
	} else {
		v.ChargeSt = oem.ChargeDC
		v.ChargeKw = v.Model.MaxDcKw
		v.chargeTarget = 80
	}
	if v.ChargeKw <= 0 {
		v.ChargeKw = 7.4
	}
	return oem.EvtChargeStart
}

// consume drains energy for km travelled; kWh/km comes from battery/range,
// scaled up with speed (aero) and down slightly in stop-go traffic.
func (v *Vehicle) consume(km float64) {
	if km <= 0 {
		return
	}
	speedFactor := 0.8 + 0.5*(v.Motion.SpeedKmh/road.MaxSpeedKmh)
	if v.hasBattery() {
		kwhPerKm := v.Model.CapacityKwh / math.Max(v.Model.RangeKm, 1)
		v.Soc -= km * kwhPerKm * speedFactor / v.Model.CapacityKwh * 100
		if v.Model.Powertrain == PHEV && v.Soc <= 0 {
			v.Fuel -= km * 6 / 100 / iceTankL * 100
		}
	} else {
		v.Fuel -= km * iceLPer100Km / 100 * speedFactor / iceTankL * 100
	}
}

func (v *Vehicle) pickDtc(rng *rand.Rand) (string, bool) {
	r := rng.Float64()
	for _, d := range dtcCatalogue {
		if r < d.weight {
			if d.battery && !v.hasBattery() {
				return "", false
			}
			for i := 0; i < int(v.nDtc); i++ {
				if v.dtc[i] == d.code {
					return "", false
				}
			}
			return d.code, true
		}
		r -= d.weight
	}
	return "", false
}

func clamp01(x float64) float64 {
	if x < 0 {
		return 0
	}
	if x > 100 {
		return 100
	}
	return x
}
