// Package config loads simulator settings from the environment (12-factor, SIM_* vars).
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the fully-resolved simulator configuration.
type Config struct {
	Vehicles     int     // SIM_VEHICLES
	RateHz       float64 // SIM_RATE_HZ events per vehicle per second
	Transport    string  // SIM_TRANSPORT kafka|mqtt|stdout|discard
	DupPct       float64 // SIM_DUP_PCT
	OooPct       float64 // SIM_OOO_PCT
	MalformedPct float64 // SIM_MALFORMED_PCT (default 0.1)
	BurstEvery   time.Duration
	BurstLen     time.Duration
	BurstFactor  int
	Duration     time.Duration // SIM_DURATION_S, 0 = forever
	KafkaBrokers []string
	KafkaTopic   string
	KafkaAcks    string // leader|all
	KafkaMaxBuf  int
	MQTTURL      string
	DatabaseURL  string
	TimeScale    float64
	Seed         uint64
	Shards       int
	Tenants      int
	TzOffsetMin  int
	MetricsAddr  string
	StdoutSample int
	LogEvery     time.Duration
	FastCohort   float64 // fraction of vehicles whose SoH degrades fast
}

// FromEnv builds a Config from the process environment with sane defaults.
func FromEnv() (Config, error) {
	c := Config{
		Vehicles:     envInt("SIM_VEHICLES", 100_000),
		RateHz:       envFloat("SIM_RATE_HZ", 1),
		Transport:    strings.ToLower(envStr("SIM_TRANSPORT", "kafka")),
		DupPct:       envFloat("SIM_DUP_PCT", 0.5),
		OooPct:       envFloat("SIM_OOO_PCT", 1.0),
		MalformedPct: envFloat("SIM_MALFORMED_PCT", 0.1),
		BurstEvery:   time.Duration(envFloat("SIM_BURST_EVERY_MIN", 10) * float64(time.Minute)),
		BurstLen:     time.Duration(envFloat("SIM_BURST_S", 60) * float64(time.Second)),
		BurstFactor:  envInt("SIM_BURST_FACTOR", 3),
		Duration:     time.Duration(envFloat("SIM_DURATION_S", 0) * float64(time.Second)),
		KafkaBrokers: strings.Split(envStr("KAFKA_BOOTSTRAP", "localhost:19092"), ","),
		KafkaTopic:   envStr("SIM_KAFKA_TOPIC", "telemetry.raw"),
		KafkaAcks:    strings.ToLower(envStr("SIM_KAFKA_ACKS", "leader")),
		KafkaMaxBuf:  envInt("SIM_KAFKA_MAX_BUFFERED", 1_000_000),
		MQTTURL:      envStr("MQTT_URL", "mqtt://localhost:1883"),
		DatabaseURL:  envStr("DATABASE_URL", ""),
		TimeScale:    envFloat("SIM_TIME_SCALE", 1),
		Seed:         uint64(envInt("SIM_SEED", 42)),
		Shards:       envInt("SIM_SHARDS", 64),
		Tenants:      envInt("SIM_TENANTS", 8),
		TzOffsetMin:  envInt("SIM_TZ_OFFSET_MIN", 330), // Asia/Kolkata
		MetricsAddr:  envStr("SIM_METRICS_ADDR", ":9101"),
		StdoutSample: envInt("SIM_STDOUT_SAMPLE", 10),
		LogEvery:     time.Duration(envFloat("SIM_LOG_EVERY_S", 5) * float64(time.Second)),
		FastCohort:   envFloat("SIM_FAST_DEGRADE_PCT", 2) / 100,
	}
	if c.Vehicles <= 0 {
		return c, fmt.Errorf("SIM_VEHICLES must be > 0")
	}
	if c.RateHz <= 0 {
		return c, fmt.Errorf("SIM_RATE_HZ must be > 0")
	}
	switch c.Transport {
	case "kafka", "mqtt", "stdout", "discard":
	default:
		return c, fmt.Errorf("SIM_TRANSPORT must be kafka|mqtt|stdout|discard, got %q", c.Transport)
	}
	if c.Shards <= 0 {
		c.Shards = 1
	}
	if c.Shards > c.Vehicles {
		c.Shards = c.Vehicles
	}
	if c.BurstFactor < 1 {
		c.BurstFactor = 1
	}
	if c.Tenants <= 0 {
		c.Tenants = 1
	}
	return c, nil
}

func envStr(k, d string) string {
	if v, ok := os.LookupEnv(k); ok && strings.TrimSpace(v) != "" {
		return strings.TrimSpace(v)
	}
	return d
}

func envInt(k string, d int) int {
	if v, ok := os.LookupEnv(k); ok && strings.TrimSpace(v) != "" {
		if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
			return n
		}
	}
	return d
}

func envFloat(k string, d float64) float64 {
	if v, ok := os.LookupEnv(k); ok && strings.TrimSpace(v) != "" {
		if f, err := strconv.ParseFloat(strings.TrimSpace(v), 64); err == nil {
			return f
		}
	}
	return d
}
