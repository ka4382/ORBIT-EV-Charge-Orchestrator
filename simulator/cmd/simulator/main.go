// Command simulator generates OEM-format vehicle telemetry for the ORBIT platform.
package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/orbit-fleet/simulator/internal/clock"
	"github.com/orbit-fleet/simulator/internal/config"
	"github.com/orbit-fleet/simulator/internal/engine"
	"github.com/orbit-fleet/simulator/internal/fleet"
	"github.com/orbit-fleet/simulator/internal/metrics"
	"github.com/orbit-fleet/simulator/internal/transport"
)

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	log.SetOutput(os.Stderr)
	cfg, err := config.FromEnv()
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	log.Printf("config: vehicles=%d rate=%.2fHz transport=%s shards=%d dup=%.2f%% ooo=%.2f%% malformed=%.2f%% burst=every %s x%d for %s timescale=%.1f seed=%d duration=%s",
		cfg.Vehicles, cfg.RateHz, cfg.Transport, cfg.Shards, cfg.DupPct, cfg.OooPct, cfg.MalformedPct, cfg.BurstEvery, cfg.BurstFactor, cfg.BurstLen, cfg.TimeScale, cfg.Seed, cfg.Duration)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if cfg.Duration > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, cfg.Duration)
		defer cancel()
	}

	var tr transport.Transport
	m := metrics.New(func() float64 {
		if tr == nil {
			return 0
		}
		return float64(tr.Buffered())
	})
	metricsCtx, metricsStop := context.WithCancel(context.Background())
	defer metricsStop()
	go func() {
		if err := m.Serve(metricsCtx, cfg.MetricsAddr); err != nil {
			log.Printf("metrics server: %v", err)
		}
	}()

	vehicles := loadFleet(ctx, cfg)
	st := engine.NewStats(m)
	tr, err = transport.New(ctx, cfg, st)
	if err != nil {
		log.Fatalf("transport: %v", err)
	}
	clk := clock.New(cfg.TimeScale, cfg.TzOffsetMin)
	eng := engine.New(cfg, clk, tr, st, m, vehicles)
	log.Printf("running %d vehicles on %d shards", len(vehicles), cfg.Shards)

	start := time.Now()
	logDone := make(chan struct{})
	go func() {
		defer close(logDone)
		summaryLoop(ctx, cfg.LogEvery, st, tr, eng, len(vehicles))
	}()
	eng.Run(ctx)
	<-logDone
	eng.FlushDelayed()

	closeCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := tr.Close(closeCtx); err != nil {
		log.Printf("transport close: %v", err)
	}
	elapsed := time.Since(start)
	sent, failed, gen, dup, ooo, mal := st.Snapshot()
	fmt.Fprintf(os.Stderr, "SUMMARY vehicles=%d duration=%.1fs sent=%d errors=%d generated=%d dup=%d ooo=%d malformed=%d achieved=%.0f msg/s\n",
		len(vehicles), elapsed.Seconds(), sent, failed, gen, dup, ooo, mal, float64(sent)/elapsed.Seconds())
}

// loadFleet returns the seeded fleet from Postgres. When DATABASE_URL is set the simulator never invents
// vehicles: it retries for up to two minutes and then exits non-zero (the supervisor restarts it). An
// earlier silent fallback streamed 2,500 made-up VINs per tenant during a database outage, which the
// platform then registered as real vehicles. Synthetic vehicles are only used when DATABASE_URL is unset
// (benchmarks and stdout mode).
func loadFleet(ctx context.Context, cfg config.Config) []fleet.Vehicle {
	if cfg.DatabaseURL != "" {
		deadline := time.Now().Add(2 * time.Minute)
		for {
			vs, err := fleet.LoadFromDB(ctx, cfg.DatabaseURL, cfg.Vehicles)
			if err == nil {
				log.Printf("loaded %d vehicles from postgres", len(vs))
				return vs
			}
			if time.Now().After(deadline) || ctx.Err() != nil {
				log.Fatalf("postgres unavailable after retries (%v); refusing to invent vehicles", err)
			}
			log.Printf("postgres unavailable (%v); retrying in 5s", err)
			select {
			case <-ctx.Done():
			case <-time.After(5 * time.Second):
			}
		}
	} else {
		log.Printf("DATABASE_URL unset; generating %d synthetic vehicles", cfg.Vehicles)
	}
	return fleet.Synthetic(cfg.Vehicles, cfg.Tenants, cfg.Seed)
}

func summaryLoop(ctx context.Context, every time.Duration, st *engine.Stats, tr transport.Transport, eng *engine.Engine, vehicles int) {
	if every <= 0 {
		every = 5 * time.Second
	}
	t := time.NewTicker(every)
	defer t.Stop()
	lastSent, _, _, _, _, _ := st.Snapshot()
	last := time.Now()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-t.C:
			sent, failed, _, _, _, _ := st.Snapshot()
			rate := float64(sent-lastSent) / now.Sub(last).Seconds()
			lastSent, last = sent, now
			burst := ""
			if eng.BurstActive() {
				burst = " burst=on"
			}
			log.Printf("vehicles=%d events/s=%.0f sent=%d errors=%d buffered=%d%s", vehicles, rate, sent, failed, tr.Buffered(), burst)
		}
	}
}
