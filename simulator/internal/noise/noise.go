// Package noise injects the imperfections a real OEM feed has: duplicates (same
// seq re-sent), out-of-order delivery (delayed 2-10 s) and rare malformed payloads.
package noise

import (
	"container/heap"
	"math/rand/v2"
)

// Decision is what to do with one freshly generated event.
type Decision struct {
	Malformed bool // corrupt the payload (DLQ exercise)
	Dup       bool // send now and again later with the same seq
	Ooo       bool // hold the event back 2-10 s so a later seq overtakes it
}

// Injector holds the configured rates (in percent).
type Injector struct {
	dup, ooo, mal float64 // fractions 0..1
	MinDelayMs    int64
	MaxDelayMs    int64
}

// New builds an injector from percentages (0.5 means 0.5 %).
func New(dupPct, oooPct, malformedPct float64) *Injector {
	return &Injector{dup: dupPct / 100, ooo: oooPct / 100, mal: malformedPct / 100, MinDelayMs: 2000, MaxDelayMs: 10000}
}

// Decide draws the fate of one event. Dup and Ooo are mutually exclusive.
func (in *Injector) Decide(rng *rand.Rand) Decision {
	var d Decision
	if in.mal > 0 && rng.Float64() < in.mal {
		d.Malformed = true
	}
	r := rng.Float64()
	switch {
	case in.dup > 0 && r < in.dup:
		d.Dup = true
	case in.ooo > 0 && r < in.dup+in.ooo:
		d.Ooo = true
	}
	return d
}

// Delay picks a hold-back duration in ms.
func (in *Injector) Delay(rng *rand.Rand) int64 {
	return in.MinDelayMs + rng.Int64N(in.MaxDelayMs-in.MinDelayMs+1)
}

// Held is an event waiting in the delay queue. Key/Value must stay immutable.
type Held struct {
	DueMs  int64
	Tenant int32
	Key    []byte
	Value  []byte
}

// DelayQueue is a min-heap on DueMs owned by one shard.
type DelayQueue struct{ h heldHeap }

// Push schedules an event.
func (q *DelayQueue) Push(h Held) { heap.Push(&q.h, h) }

// Len is the number of held events.
func (q *DelayQueue) Len() int { return len(q.h) }

// PopDue pops the earliest event if it is due at nowMs.
func (q *DelayQueue) PopDue(nowMs int64) (Held, bool) {
	if len(q.h) == 0 || q.h[0].DueMs > nowMs {
		return Held{}, false
	}
	return heap.Pop(&q.h).(Held), true
}

type heldHeap []Held

func (h heldHeap) Len() int           { return len(h) }
func (h heldHeap) Less(i, j int) bool { return h[i].DueMs < h[j].DueMs }
func (h heldHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *heldHeap) Push(x any)        { *h = append(*h, x.(Held)) }
func (h *heldHeap) Pop() any {
	old := *h
	n := len(old)
	x := old[n-1]
	*h = old[:n-1]
	return x
}
