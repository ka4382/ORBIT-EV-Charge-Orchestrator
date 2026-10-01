package transport

import (
	"context"
	"fmt"
	"math"
	"sync/atomic"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/orbit-fleet/simulator/internal/config"
)

// Kafka publishes to telemetry.raw keyed by VIN using franz-go.
type Kafka struct {
	cl       *kgo.Client
	topic    string
	inflight atomic.Int64
	st       Stats
}

// NewKafka connects and verifies the brokers are reachable.
func NewKafka(ctx context.Context, cfg config.Config, st Stats) (*Kafka, error) {
	opts := []kgo.Opt{
		kgo.SeedBrokers(cfg.KafkaBrokers...),
		kgo.ClientID("orbit-simulator"),
		kgo.DefaultProduceTopic(cfg.KafkaTopic),
		kgo.ProducerBatchCompression(kgo.Lz4Compression()),
		kgo.ProducerLinger(20 * time.Millisecond),
		kgo.ProducerBatchMaxBytes(1 << 20),
		kgo.MaxBufferedRecords(cfg.KafkaMaxBuf),
		kgo.RecordPartitioner(kgo.StickyKeyPartitioner(nil)), // murmur2 hash of key = VIN
		kgo.AllowAutoTopicCreation(),
		// No silent loss during a broker outage: retry until the record is 5 minutes old instead of
		// giving up after a fixed count. MaxBufferedRecords bounds memory and back-pressures the
		// generator, so an outage turns into buffering and lag rather than dropped telemetry.
		kgo.RecordRetries(math.MaxInt32),
		kgo.UnknownTopicRetries(-1),
		kgo.RecordDeliveryTimeout(5 * time.Minute),
		kgo.ProduceRequestTimeout(10 * time.Second),
		kgo.MaxProduceRequestsInflightPerBroker(5),
	}
	if cfg.KafkaAcks == "leader" {
		// Telemetry is at-least-once with idempotent consumers (ADR-003): leader acks
		// and no producer idempotence give the best laptop throughput.
		opts = append(opts, kgo.RequiredAcks(kgo.LeaderAck()), kgo.DisableIdempotentWrite())
	} else {
		opts = append(opts, kgo.RequiredAcks(kgo.AllISRAcks()))
	}
	cl, err := kgo.NewClient(opts...)
	if err != nil {
		return nil, fmt.Errorf("kafka client: %w", err)
	}
	pingCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := cl.Ping(pingCtx); err != nil {
		cl.Close()
		return nil, fmt.Errorf("kafka ping %v: %w", cfg.KafkaBrokers, err)
	}
	return &Kafka{cl: cl, topic: cfg.KafkaTopic, st: st}, nil
}

// Send implements Transport. Back-pressure: when MaxBufferedRecords is reached
// Produce blocks the calling shard, which naturally throttles generation.
func (k *Kafka) Send(_ int32, key, value []byte) {
	k.inflight.Add(1)
	k.cl.Produce(context.Background(), &kgo.Record{Key: key, Value: value}, k.onDone)
}

func (k *Kafka) onDone(_ *kgo.Record, err error) {
	k.inflight.Add(-1)
	if err != nil {
		k.st.Failed(1)
		return
	}
	k.st.Sent(1)
}

// Buffered implements Transport.
func (k *Kafka) Buffered() int64 { return int64(k.cl.BufferedProduceRecords()) }

// Close implements Transport: flush everything buffered, then close.
func (k *Kafka) Close(ctx context.Context) error {
	err := k.cl.Flush(ctx)
	k.cl.Close()
	return err
}
