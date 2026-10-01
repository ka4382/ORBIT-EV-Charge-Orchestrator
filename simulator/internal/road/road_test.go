package road

import (
	"math/rand/v2"
	"testing"
)

func TestWalkStaysInBounds(t *testing.T) {
	rng := rand.New(rand.NewPCG(7, 7))
	b := Bounds{Centre: Pos{13.0827, 80.2707}, RadiusDeg: DefaultRadiusDeg}
	m := &Motion{Pos: b.Centre}
	Start(m, rng)
	total := 0.0
	for i := 0; i < 500_000; i++ {
		dt := 1.0
		if i%1000 == 0 {
			dt = 60 // time-scaled run
		}
		km, _ := Step(m, b, dt, rng)
		total += km
		if km < 0 {
			t.Fatalf("negative distance %v", km)
		}
		if m.SpeedKmh < 0 || m.SpeedKmh > MaxSpeedKmh {
			t.Fatalf("speed out of range: %v", m.SpeedKmh)
		}
		if !b.Contains(m.Pos) {
			t.Fatalf("step %d left bounds: %+v", i, m.Pos)
		}
	}
	if total < 100 {
		t.Fatalf("vehicle barely moved: %.1f km", total)
	}
}

func TestZeroDt(t *testing.T) {
	rng := rand.New(rand.NewPCG(1, 1))
	m := &Motion{Pos: Pos{1, 1}, SpeedKmh: 50}
	km, hb := Step(m, Bounds{Centre: Pos{1, 1}, RadiusDeg: 1}, 0, rng)
	if km != 0 || hb {
		t.Fatal("zero dt must be a no-op")
	}
}
