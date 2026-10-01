// Package engine runs the fleet: a worker pool sharded by VIN, each shard owning
// its vehicles and ticking on a jittered schedule, encoding into pre-allocated
// arenas and handing records to the transport.
package engine

import (
	"context"
	"log"
	"math/rand/v2"
	"sync"
	"sync/atomic"
	"time"

	"github.com/orbit-fleet/simulator/internal/clock"
	"github.com/orbit-fleet/simulator/internal/config"
	"github.com/orbit-fleet/simulator/internal/fleet"
	"github.com/orbit-fleet/simulator/internal/metrics"
	"github.com/orbit-fleet/simulator/internal/noise"
	"github.com/orbit-fleet/simulator/internal/oem"
	"github.com/orbit-fleet/simulator/internal/transport"
)

const (
	arenaSize  = 256 << 10 // bytes per shard arena; records slice into it
	arenaSlack = 1024      // a payload never exceeds this
	minTick    = 10 * time.Millisecond
	maxTick    = 100 * time.Millisecond
)

// Stats counts delivery outcomes for the log line and Prometheus.
type Stats struct {
	sent, failed, generated, dup, ooo, malformed atomic.Int64
	m                                            *metrics.Metrics
}

// NewStats binds counters to the Prometheus set (m may be nil in tests).
func NewStats(m *metrics.Metrics) *Stats { return &Stats{m: m} }

// Sent implements transport.Stats.
func (s *Stats) Sent(n int) {
	s.sent.Add(int64(n))
	if s.m != nil {
		s.m.Sent.Add(float64(n))
	}
}

// Failed implements transport.Stats.
func (s *Stats) Failed(n int) {
	s.failed.Add(int64(n))
	if s.m != nil {
		s.m.SendErrors.Add(float64(n))
	}
}

// Snapshot returns the current counters.
func (s *Stats) Snapshot() (sent, failed, generated, dup, ooo, malformed int64) {
	return s.sent.Load(), s.failed.Load(), s.generated.Load(), s.dup.Load(), s.ooo.Load(), s.malformed.Load()
}

// Engine drives all shards.
type Engine struct {
	cfg      config.Config
	clk      *clock.Clock
	tr       transport.Transport
	st       *Stats
	m        *metrics.Metrics
	vehicles []fleet.Vehicle
	shards   []*shard
	burst    atomic.Bool
}

// New assigns vehicles to shards (vehicle i -> shard i % shards) and initialises them.
func New(cfg config.Config, clk *clock.Clock, tr transport.Transport, st *Stats, m *metrics.Metrics, vehicles []fleet.Vehicle) *Engine {
	e := &Engine{cfg: cfg, clk: clk, tr: tr, st: st, m: m, vehicles: vehicles}
	nShards := cfg.Shards
	if nShards > len(vehicles) {
		nShards = len(vehicles)
	}
	if nShards < 1 {
		nShards = 1
	}
	e.shards = make([]*shard, nShards)
	for i := range e.shards {
		e.shards[i] = newShard(e, i, nShards)
	}
	// Mark the fast-degrading cohort deterministically from the seed.
	rng := rand.New(rand.NewPCG(cfg.Seed, 0xfa57))
	simNow := clk.Now()
	for i := range vehicles {
		v := &vehicles[i]
		v.FastDegrade = rng.Float64() < cfg.FastCohort
		v.Init(e.shards[i%nShards].rng, simNow)
	}
	if m != nil {
		m.Vehicles.Set(float64(len(vehicles)))
	}
	return e
}

// Run blocks until ctx is cancelled.
func (e *Engine) Run(ctx context.Context) {
	var wg sync.WaitGroup
	if e.cfg.BurstEvery > 0 && e.cfg.BurstFactor > 1 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			e.burstLoop(ctx)
		}()
	}
	for _, s := range e.shards {
		wg.Add(1)
		go func(s *shard) {
			defer wg.Done()
			s.run(ctx)
		}(s)
	}
	wg.Wait()
}

// BurstActive reports whether the shift-start burst is on.
func (e *Engine) BurstActive() bool { return e.burst.Load() }

func (e *Engine) burstLoop(ctx context.Context) {
	t := time.NewTicker(e.cfg.BurstEvery)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		log.Printf("burst: %dx rate for %s (shift start)", e.cfg.BurstFactor, e.cfg.BurstLen)
		e.burst.Store(true)
		if e.m != nil {
			e.m.BurstOn.Set(1)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(e.cfg.BurstLen):
		}
		e.burst.Store(false)
		if e.m != nil {
			e.m.BurstOn.Set(0)
		}
	}
}

// shard owns a strided subset of the fleet.
type shard struct {
	e     *Engine
	id    int
	idx   []int // indices into e.vehicles
	rng   *rand.Rand
	inj   *noise.Injector
	dq    noise.DelayQueue
	arena []byte
	ev    oem.Event
}

func newShard(e *Engine, id, n int) *shard {
	s := &shard{e: e, id: id, rng: rand.New(rand.NewPCG(e.cfg.Seed, uint64(id)+1)),
		inj: noise.New(e.cfg.DupPct, e.cfg.OooPct, e.cfg.MalformedPct), arena: make([]byte, 0, arenaSize)}
	for i := id; i < len(e.vehicles); i += n {
		s.idx = append(s.idx, i)
	}
	return s
}

// schedule splits the shard's vehicles into chunks so ticks land between minTick and maxTick.
func (s *shard) schedule() (period time.Duration, chunks int) {
	period = time.Duration(float64(time.Second) / s.e.cfg.RateHz)
	chunks = int(period / (50 * time.Millisecond))
	if chunks < 1 {
		chunks = 1
	}
	if chunks > len(s.idx) {
		chunks = len(s.idx)
	}
	for chunks > 1 && period/time.Duration(chunks) < minTick {
		chunks--
	}
	for period/time.Duration(chunks) > maxTick && chunks < len(s.idx) {
		chunks++
	}
	return period, chunks
}

func (s *shard) run(ctx context.Context) {
	if len(s.idx) == 0 {
		return
	}
	period, chunks := s.schedule()
	chunkSize := (len(s.idx) + chunks - 1) / chunks
	// Jitter: shards start at different phases so load is flat.
	time.Sleep(time.Duration(s.rng.Float64() * float64(period) / float64(chunks)))
	next := time.Now()
	chunk := 0
	for {
		factor := 1
		if s.e.burst.Load() {
			factor = s.e.cfg.BurstFactor
		}
		interval := period / time.Duration(chunks) / time.Duration(factor)

		lo := chunk * chunkSize
		hi := lo + chunkSize
		if hi > len(s.idx) {
			hi = len(s.idx)
		}
		s.tick(s.idx[lo:hi])
		chunk++
		if chunk >= chunks {
			chunk = 0
		}
		s.drainDelayed(time.Now().UnixMilli())

		next = next.Add(interval)
		now := time.Now()
		if next.Before(now) {
			next = now // fell behind: do not spiral
		} else {
			select {
			case <-ctx.Done():
				return
			case <-time.After(next.Sub(now)):
			}
		}
		select {
		case <-ctx.Done():
			return
		default:
		}
	}
}

// tick advances and publishes one chunk of vehicles.
func (s *shard) tick(ids []int) {
	simNow := s.e.clk.Now()
	hour := s.e.clk.LocalHour(simNow)
	realNow := time.Now().UnixMilli()
	var gen, dup, ooo, mal int64
	for _, i := range ids {
		v := &s.e.vehicles[i]
		v.Step(simNow, hour, s.rng, &s.ev)
		d := s.inj.Decide(s.rng)
		corrupt := oem.Clean
		if d.Malformed {
			mal++
			if s.rng.IntN(2) == 0 {
				corrupt = oem.BadVIN
			} else {
				corrupt = oem.MissingField
			}
		}
		key, val := s.encode(v.OEM, corrupt)
		gen++
		switch {
		case d.Dup:
			dup++
			s.e.tr.Send(v.TenantID, key, val)
			s.dq.Push(noise.Held{DueMs: realNow + s.inj.Delay(s.rng), Tenant: v.TenantID, Key: key, Value: val})
		case d.Ooo:
			ooo++
			s.dq.Push(noise.Held{DueMs: realNow + s.inj.Delay(s.rng), Tenant: v.TenantID, Key: key, Value: val})
		default:
			s.e.tr.Send(v.TenantID, key, val)
		}
	}
	s.e.st.generated.Add(gen)
	s.e.st.dup.Add(dup)
	s.e.st.ooo.Add(ooo)
	s.e.st.malformed.Add(mal)
	if m := s.e.m; m != nil {
		m.Generated.Add(float64(gen))
		if dup > 0 {
			m.Noise.WithLabelValues("dup").Add(float64(dup))
		}
		if ooo > 0 {
			m.Noise.WithLabelValues("ooo").Add(float64(ooo))
		}
		if mal > 0 {
			m.Noise.WithLabelValues("malformed").Add(float64(mal))
		}
	}
}

// encode writes key (VIN) and value into the arena and returns immutable slices.
func (s *shard) encode(kind oem.Kind, m oem.Malformed) (key, val []byte) {
	if cap(s.arena)-len(s.arena) < arenaSlack {
		s.arena = make([]byte, 0, arenaSize) // old arena stays alive while records reference it
	}
	k0 := len(s.arena)
	s.arena = append(s.arena, s.ev.VIN[:]...)
	key = s.arena[k0:len(s.arena):len(s.arena)]
	v0 := len(s.arena)
	s.arena = oem.Encode(s.arena, kind, &s.ev, m)
	val = s.arena[v0:len(s.arena):len(s.arena)]
	return key, val
}

func (s *shard) drainDelayed(nowMs int64) {
	for {
		h, ok := s.dq.PopDue(nowMs)
		if !ok {
			return
		}
		s.e.tr.Send(h.Tenant, h.Key, h.Value)
	}
}

// FlushDelayed sends everything still held back (used at shutdown).
func (e *Engine) FlushDelayed() {
	for _, s := range e.shards {
		s.drainDelayed(1<<62 - 1)
	}
}
