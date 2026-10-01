package noise

import (
	"math"
	"math/rand/v2"
	"testing"
)

func TestRatesWithinTolerance(t *testing.T) {
	in := New(0.5, 1.0, 0.1)
	rng := rand.New(rand.NewPCG(11, 11))
	const n = 2_000_000
	var dup, ooo, mal int
	for i := 0; i < n; i++ {
		d := in.Decide(rng)
		if d.Dup && d.Ooo {
			t.Fatal("dup and ooo must be exclusive")
		}
		if d.Dup {
			dup++
		}
		if d.Ooo {
			ooo++
		}
		if d.Malformed {
			mal++
		}
	}
	check := func(name string, got int, wantPct float64) {
		gotPct := float64(got) / n * 100
		if math.Abs(gotPct-wantPct)/wantPct > 0.1 {
			t.Errorf("%s rate %.3f%% not within 10%% of %.3f%%", name, gotPct, wantPct)
		}
	}
	check("dup", dup, 0.5)
	check("ooo", ooo, 1.0)
	check("malformed", mal, 0.1)
}

func TestZeroRates(t *testing.T) {
	in := New(0, 0, 0)
	rng := rand.New(rand.NewPCG(1, 1))
	for i := 0; i < 100000; i++ {
		if d := in.Decide(rng); d.Dup || d.Ooo || d.Malformed {
			t.Fatal("zero rates produced noise")
		}
	}
}

func TestDelayQueueOrderAndRange(t *testing.T) {
	in := New(1, 1, 0)
	rng := rand.New(rand.NewPCG(4, 4))
	var q DelayQueue
	for i := 0; i < 1000; i++ {
		d := in.Delay(rng)
		if d < 2000 || d > 10000 {
			t.Fatalf("delay %d outside 2-10 s", d)
		}
		q.Push(Held{DueMs: d, Key: []byte("k")})
	}
	if _, ok := q.PopDue(1999); ok {
		t.Fatal("nothing should be due before 2 s")
	}
	last := int64(0)
	n := 0
	for {
		h, ok := q.PopDue(10000)
		if !ok {
			break
		}
		if h.DueMs < last {
			t.Fatal("queue not ordered")
		}
		last = h.DueMs
		n++
	}
	if n != 1000 || q.Len() != 0 {
		t.Fatalf("popped %d, remaining %d", n, q.Len())
	}
}
