// Package clock provides the simulated clock: wall time scaled by SIM_TIME_SCALE.
package clock

import "time"

// Clock maps real elapsed time to simulated time: sim = epoch + (real - start) * scale.
type Clock struct {
	epoch time.Time
	start time.Time
	scale float64
	tzOff time.Duration // local offset for shift patterns (Asia/Kolkata by default)
}

// New returns a clock whose simulated time equals wall time at start when scale == 1.
func New(scale float64, tzOffsetMin int) *Clock {
	if scale <= 0 {
		scale = 1
	}
	now := time.Now()
	return &Clock{epoch: now, start: now, scale: scale, tzOff: time.Duration(tzOffsetMin) * time.Minute}
}

// Now returns the simulated time as unix milliseconds.
func (c *Clock) Now() int64 {
	return c.At(time.Now())
}

// At converts a real instant to simulated unix milliseconds.
func (c *Clock) At(real time.Time) int64 {
	elapsed := float64(real.Sub(c.start)) * c.scale
	return c.epoch.UnixMilli() + int64(elapsed/1e6)
}

// Scale is the simulated seconds per real second.
func (c *Clock) Scale() float64 { return c.scale }

// LocalHour returns the local hour-of-day (0-23, fractional) for a simulated ms timestamp.
func (c *Clock) LocalHour(ms int64) float64 {
	local := ms + c.tzOff.Milliseconds()
	dayMs := local % 86_400_000
	if dayMs < 0 {
		dayMs += 86_400_000
	}
	return float64(dayMs) / 3_600_000
}
