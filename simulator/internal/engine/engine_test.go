package engine

import (
	"context"
	"encoding/json"
	"sync"
	"testing"
	"time"

	"github.com/orbit-fleet/simulator/internal/clock"
	"github.com/orbit-fleet/simulator/internal/config"
	"github.com/orbit-fleet/simulator/internal/fleet"
	"github.com/orbit-fleet/simulator/internal/vin"
)

type capture struct {
	mu   sync.Mutex
	seen map[string][]uint64 // vin -> seqs in arrival order
	n    int
	st   *Stats
}

func (c *capture) Send(_ int32, key, value []byte) {
	var seq struct {
		Seq *uint64 `json:"seq"`
		N   *uint64 `json:"n"`
	}
	_ = json.Unmarshal(value, &seq)
	s := seq.Seq
	if s == nil {
		s = seq.N
	}
	c.mu.Lock()
	if s != nil {
		c.seen[string(key)] = append(c.seen[string(key)], *s)
	}
	c.n++
	c.mu.Unlock()
	c.st.Sent(1)
}
func (c *capture) Buffered() int64             { return 0 }
func (c *capture) Close(context.Context) error { return nil }

func TestEngineProducesPerVehicleMonotonicSeqWithNoise(t *testing.T) {
	cfg := config.Config{Vehicles: 200, RateHz: 20, DupPct: 5, OooPct: 5, MalformedPct: 1, Shards: 8, Seed: 1, Tenants: 4, BurstFactor: 1}
	st := NewStats(nil)
	cap := &capture{seen: map[string][]uint64{}, st: st}
	clk := clock.New(60, 330)
	vs := fleet.Synthetic(cfg.Vehicles, cfg.Tenants, cfg.Seed)
	e := New(cfg, clk, cap, st, nil, vs)
	ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
	defer cancel()
	e.Run(ctx)
	e.FlushDelayed()

	sent, _, gen, dup, ooo, mal := st.Snapshot()
	if gen < int64(cfg.Vehicles)*10 {
		t.Fatalf("too few events generated: %d", gen)
	}
	if sent != gen+dup {
		t.Fatalf("sent %d != generated %d + dup %d", sent, gen, dup)
	}
	if dup == 0 || ooo == 0 || mal == 0 {
		t.Fatalf("noise not injected: dup=%d ooo=%d mal=%d", dup, ooo, mal)
	}
	var badVin, dupSeen, oooSeen int
	for k, seqs := range cap.seen {
		if !vin.IsValid(k) {
			badVin++
		}
		var last uint64
		for _, s := range seqs {
			switch {
			case s == last:
				dupSeen++
			case s < last:
				oooSeen++
			}
			if s > last {
				last = s
			}
		}
	}
	if badVin != 0 {
		t.Fatalf("record keys must always be the true VIN, got %d bad keys", badVin)
	}
	if dupSeen == 0 {
		t.Fatal("no duplicate seq observed on the wire")
	}
	if oooSeen == 0 {
		t.Fatal("no out-of-order seq observed on the wire")
	}
}

func TestScheduleBounds(t *testing.T) {
	for _, hz := range []float64{0.1, 1, 10, 100} {
		cfg := config.Config{RateHz: hz, Shards: 1, Seed: 1}
		e := &Engine{cfg: cfg}
		e.vehicles = make([]fleet.Vehicle, 1000)
		s := newShard(e, 0, 1)
		period, chunks := s.schedule()
		tick := period / time.Duration(chunks)
		if tick < minTick || (tick > maxTick && chunks < len(s.idx)) {
			t.Fatalf("hz=%v tick %v chunks %d out of bounds", hz, tick, chunks)
		}
	}
}
