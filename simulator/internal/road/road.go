// Package road provides synthetic road movement: a random walk along a heading with
// speed noise, kept inside a bounding box around a city centre (vehicles reflect at
// the edge like hitting the ring road).
package road

import (
	"math"
	"math/rand/v2"
)

const (
	// MaxSpeedKmh is the urban speed cap.
	MaxSpeedKmh = 90.0
	// DefaultRadiusDeg is the half-size of the box around the city centre (~35 km).
	DefaultRadiusDeg = 0.35
	kmPerDegLat      = 111.32
)

// Pos is a WGS84 position in degrees.
type Pos struct{ Lat, Lon float64 }

// Motion is the mutable kinematic state of a vehicle on a trip.
type Motion struct {
	Pos        Pos
	HeadingRad float64
	SpeedKmh   float64
	TargetKmh  float64
}

// Bounds is the drivable box around a city centre.
type Bounds struct {
	Centre    Pos
	RadiusDeg float64
}

// Contains reports whether p lies inside the box.
func (b Bounds) Contains(p Pos) bool {
	return math.Abs(p.Lat-b.Centre.Lat) <= b.RadiusDeg && math.Abs(p.Lon-b.Centre.Lon) <= b.RadiusDeg
}

// Start initialises a trip: random heading, random cruise target.
func Start(m *Motion, rng *rand.Rand) {
	m.HeadingRad = rng.Float64() * 2 * math.Pi
	m.TargetKmh = 20 + rng.Float64()*(MaxSpeedKmh-20)
	m.SpeedKmh = 0
}

// Step advances the motion by dtSec seconds and returns distance travelled in km and
// whether a harsh brake occurred (speed dropped by more than 25 km/h in one step).
// Speed is always within [0, MaxSpeedKmh] and the position stays inside b.
func Step(m *Motion, b Bounds, dtSec float64, rng *rand.Rand) (km float64, harshBrake bool) {
	if dtSec <= 0 {
		return 0, false
	}
	// Occasionally pick a new cruise target (traffic lights, junctions).
	if rng.Float64() < 0.05*minf(dtSec, 5) {
		if rng.Float64() < 0.15 {
			m.TargetKmh = 0 // stop at a light
		} else {
			m.TargetKmh = 15 + rng.Float64()*(MaxSpeedKmh-15)
		}
	}
	prev := m.SpeedKmh
	// Approach target with bounded accel (~2.5 m/s^2 = 9 km/h per s) plus noise.
	maxDelta := 9.0 * minf(dtSec, 10)
	delta := m.TargetKmh - m.SpeedKmh
	if delta > maxDelta {
		delta = maxDelta
	} else if delta < -maxDelta*1.6 {
		delta = -maxDelta * 1.6 // braking is stronger than acceleration
	}
	m.SpeedKmh += delta + (rng.Float64()-0.5)*4
	if m.SpeedKmh < 0 {
		m.SpeedKmh = 0
	} else if m.SpeedKmh > MaxSpeedKmh {
		m.SpeedKmh = MaxSpeedKmh
	}
	harshBrake = prev-m.SpeedKmh > 25

	// Heading random walk (roads bend), occasional turn.
	m.HeadingRad += (rng.Float64() - 0.5) * 0.3
	if rng.Float64() < 0.02 {
		m.HeadingRad += (rng.Float64() - 0.5) * math.Pi
	}

	km = (prev + m.SpeedKmh) / 2 * dtSec / 3600
	dLat := km * math.Cos(m.HeadingRad) / kmPerDegLat
	dLon := km * math.Sin(m.HeadingRad) / (kmPerDegLat * math.Cos(m.Pos.Lat*math.Pi/180))
	m.Pos.Lat += dLat
	m.Pos.Lon += dLon
	if !b.Contains(m.Pos) {
		// Reflect: turn around and clamp.
		m.HeadingRad += math.Pi
		m.Pos.Lat = clamp(m.Pos.Lat, b.Centre.Lat-b.RadiusDeg, b.Centre.Lat+b.RadiusDeg)
		m.Pos.Lon = clamp(m.Pos.Lon, b.Centre.Lon-b.RadiusDeg, b.Centre.Lon+b.RadiusDeg)
	}
	return km, harshBrake
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func minf(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}

// DistanceKm is the equirectangular distance between two positions (fine for < 100 km).
func DistanceKm(a, b Pos) float64 {
	dLat := (b.Lat - a.Lat) * kmPerDegLat
	dLon := (b.Lon - a.Lon) * kmPerDegLat * math.Cos((a.Lat+b.Lat)/2*math.Pi/180)
	return math.Sqrt(dLat*dLat + dLon*dLon)
}

// SteerTo nudges the heading toward target (with road-like noise) so a vehicle
// gradually returns to its depot instead of wandering forever.
func SteerTo(m *Motion, target Pos, rng *rand.Rand) {
	dLat := target.Lat - m.Pos.Lat
	dLon := (target.Lon - m.Pos.Lon) * math.Cos(m.Pos.Lat*math.Pi/180)
	want := math.Atan2(dLon, dLat)
	diff := math.Mod(want-m.HeadingRad+3*math.Pi, 2*math.Pi) - math.Pi
	m.HeadingRad += diff*0.5 + (rng.Float64()-0.5)*0.2
}
