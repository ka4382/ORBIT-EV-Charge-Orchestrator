// Package metrics exposes Prometheus counters on :9101 plus /healthz.
package metrics

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Metrics is the registry of simulator metrics.
type Metrics struct {
	reg        *prometheus.Registry
	Sent       prometheus.Counter
	SendErrors prometheus.Counter
	Generated  prometheus.Counter
	Vehicles   prometheus.Gauge
	Noise      *prometheus.CounterVec
	BurstOn    prometheus.Gauge
}

// New creates the metric set; buffered reports the transport's in-flight records.
func New(buffered func() float64) *Metrics {
	m := &Metrics{
		reg:        prometheus.NewRegistry(),
		Sent:       prometheus.NewCounter(prometheus.CounterOpts{Name: "sim_events_sent_total", Help: "Events handed to the transport"}),
		SendErrors: prometheus.NewCounter(prometheus.CounterOpts{Name: "sim_send_errors_total", Help: "Transport send failures"}),
		Generated:  prometheus.NewCounter(prometheus.CounterOpts{Name: "sim_events_generated_total", Help: "Events produced by the state machines"}),
		Vehicles:   prometheus.NewGauge(prometheus.GaugeOpts{Name: "sim_vehicles", Help: "Simulated vehicles"}),
		Noise:      prometheus.NewCounterVec(prometheus.CounterOpts{Name: "sim_noise_total", Help: "Injected imperfections"}, []string{"kind"}),
		BurstOn:    prometheus.NewGauge(prometheus.GaugeOpts{Name: "sim_burst_active", Help: "1 while a shift-start burst is running"}),
	}
	m.reg.MustRegister(m.Sent, m.SendErrors, m.Generated, m.Vehicles, m.Noise, m.BurstOn,
		prometheus.NewGaugeFunc(prometheus.GaugeOpts{Name: "sim_buffered_records", Help: "Records buffered in the transport"}, buffered),
		collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	return m
}

// Serve runs the HTTP endpoint until ctx is cancelled.
func (m *Metrics) Serve(ctx context.Context, addr string) error {
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(m.reg, promhttp.HandlerOpts{}))
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok\n"))
	})
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
	}()
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
