package transport

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
)

type counter struct{ sent, failed atomic.Int64 }

func (c *counter) Sent(n int)   { c.sent.Add(int64(n)) }
func (c *counter) Failed(n int) { c.failed.Add(int64(n)) }

func TestStdoutSamplesThenDiscards(t *testing.T) {
	c := &counter{}
	s := NewStdout(3, c)
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 1000; i++ {
				s.Send(1, []byte("K"), []byte(`{"x":1}`))
			}
		}()
	}
	wg.Wait()
	if err := s.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	if c.sent.Load() != 8000 || s.printed.Load() != 3 {
		t.Fatalf("sent=%d printed=%d", c.sent.Load(), s.printed.Load())
	}
}

func BenchmarkDiscardSend(b *testing.B) {
	s := NewStdout(0, &counter{})
	v := []byte(`{"oem":"OEM_A"}`)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		s.Send(1, v[:5], v)
	}
}
