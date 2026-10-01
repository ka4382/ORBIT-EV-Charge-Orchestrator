package clock

import (
	"testing"
	"time"
)

func TestScaleAndLocalHour(t *testing.T) {
	c := New(60, 330) // 1 real second = 1 simulated minute, IST
	base := c.At(c.start)
	if got := c.At(c.start.Add(10 * time.Second)); got-base != 600_000 {
		t.Fatalf("scaled elapsed = %d ms, want 600000", got-base)
	}
	// 2026-09-25T10:15:02Z is 15:45 IST.
	if h := c.LocalHour(1758795302120); h < 15.74 || h > 15.76 {
		t.Fatalf("local hour = %v, want ~15.75", h)
	}
	if New(0, 0).Scale() != 1 {
		t.Fatal("non-positive scale must default to 1")
	}
}
