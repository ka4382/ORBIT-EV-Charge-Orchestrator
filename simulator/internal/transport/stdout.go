package transport

import (
	"bufio"
	"context"
	"os"
	"sync"
	"sync/atomic"
)

// Stdout prints the first `sample` payloads (one per line) then discards the
// rest, so it doubles as the zero-cost benchmark sink.
type Stdout struct {
	sample  int64
	printed atomic.Int64
	mu      sync.Mutex
	w       *bufio.Writer
	st      Stats
}

// NewStdout creates the stdout/discard transport.
func NewStdout(sample int, st Stats) *Stdout {
	return &Stdout{sample: int64(sample), w: bufio.NewWriterSize(os.Stdout, 64<<10), st: st}
}

// Send implements Transport.
func (s *Stdout) Send(_ int32, _, value []byte) {
	if s.printed.Load() < s.sample {
		if n := s.printed.Add(1); n <= s.sample {
			s.mu.Lock()
			_, _ = s.w.Write(value)
			_ = s.w.WriteByte('\n')
			if n == s.sample {
				_ = s.w.Flush()
			}
			s.mu.Unlock()
		}
	}
	s.st.Sent(1)
}

// Buffered implements Transport.
func (s *Stdout) Buffered() int64 { return 0 }

// Close implements Transport.
func (s *Stdout) Close(context.Context) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.w.Flush()
}
