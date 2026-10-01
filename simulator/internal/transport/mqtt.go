package transport

import (
	"context"
	"fmt"
	"strconv"
	"sync/atomic"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"

	"github.com/orbit-fleet/simulator/internal/config"
)

// MQTT publishes to fleet/{tenant_id}/{vin} at QoS 1 (bridged into Kafka by EMQX).
type MQTT struct {
	cl       mqtt.Client
	inflight atomic.Int64
	st       Stats
}

// NewMQTT connects to MQTT_URL.
func NewMQTT(cfg config.Config, st Stats) (*MQTT, error) {
	opts := mqtt.NewClientOptions().
		AddBroker(cfg.MQTTURL).
		SetClientID("orbit-simulator-" + strconv.FormatInt(time.Now().UnixNano()%1_000_000, 36)).
		SetOrderMatters(false).
		SetAutoReconnect(true).
		SetConnectRetry(true).
		SetConnectTimeout(10 * time.Second).
		SetMaxResumePubInFlight(4096).
		SetWriteTimeout(5 * time.Second)
	cl := mqtt.NewClient(opts)
	tok := cl.Connect()
	if !tok.WaitTimeout(15*time.Second) || tok.Error() != nil {
		return nil, fmt.Errorf("mqtt connect %s: %v", cfg.MQTTURL, tok.Error())
	}
	return &MQTT{cl: cl, st: st}, nil
}

// Send implements Transport. QoS 1 publishes are tracked via their token in a
// bounded way: the token is polled asynchronously by paho, we count completion.
func (m *MQTT) Send(tenant int32, key, value []byte) {
	var topic [64]byte
	t := append(topic[:0], "fleet/"...)
	t = strconv.AppendInt(t, int64(tenant), 10)
	t = append(t, '/')
	t = append(t, key...)
	if !m.cl.IsConnectionOpen() {
		m.st.Failed(1)
		return
	}
	m.inflight.Add(1)
	tok := m.cl.Publish(string(t), 1, false, value)
	go func() {
		tok.Wait()
		m.inflight.Add(-1)
		if tok.Error() != nil {
			m.st.Failed(1)
		} else {
			m.st.Sent(1)
		}
	}()
}

// Buffered implements Transport.
func (m *MQTT) Buffered() int64 { return m.inflight.Load() }

// Close implements Transport.
func (m *MQTT) Close(ctx context.Context) error {
	deadline := time.Now().Add(5 * time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	for m.inflight.Load() > 0 && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	m.cl.Disconnect(250)
	return nil
}
