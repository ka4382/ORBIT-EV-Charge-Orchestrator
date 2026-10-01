// Package transport delivers encoded payloads: Kafka (franz-go), MQTT (paho),
// stdout (sample then discard) or discard (throughput benchmarks).
package transport

import (
	"context"
	"fmt"
	"strings"

	"github.com/orbit-fleet/simulator/internal/config"
)

// Stats receives delivery outcomes; implementations must be goroutine-safe.
type Stats interface {
	Sent(n int)
	Failed(n int)
}

// Transport is an asynchronous, fire-and-forget publisher.
type Transport interface {
	// Send enqueues one record. key is the VIN; the slices are immutable after the call.
	Send(tenant int32, key, value []byte)
	// Buffered is the number of records not yet acknowledged.
	Buffered() int64
	// Close flushes and releases resources.
	Close(ctx context.Context) error
}

// New builds the transport selected by cfg.Transport.
func New(ctx context.Context, cfg config.Config, st Stats) (Transport, error) {
	switch strings.ToLower(cfg.Transport) {
	case "kafka":
		return NewKafka(ctx, cfg, st)
	case "mqtt":
		return NewMQTT(cfg, st)
	case "stdout":
		return NewStdout(cfg.StdoutSample, st), nil
	case "discard":
		return NewStdout(0, st), nil
	}
	return nil, fmt.Errorf("unknown transport %q", cfg.Transport)
}
