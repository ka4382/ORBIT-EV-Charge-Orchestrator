// Package vin mirrors services/common/orbit_common/vin.py: 17 chars, no I/O/Q,
// ISO 3779 check digit at position 9 (index 8).
package vin

import (
	"errors"
	"math/rand/v2"
)

// Chars is the allowed VIN alphabet (no I, O, Q).
const Chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"

var weights = [17]int{8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2}

// translit maps a byte to its ISO 3779 value; -1 marks an illegal character.
var translit [256]int8

func init() {
	for i := range translit {
		translit[i] = -1
	}
	letters := "ABCDEFGHJKLMNPRSTUVWXYZ"
	values := []int8{1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9}
	for i := range letters {
		translit[letters[i]] = values[i]
	}
	for c := byte('0'); c <= '9'; c++ {
		translit[c] = int8(c - '0')
	}
}

// ErrCharset is returned by MakeValid for a VIN outside the allowed alphabet/length.
var ErrCharset = errors.New("bad VIN charset")

// CheckDigit computes the check digit for a 17-byte VIN; the 9th char is ignored.
// The caller must ensure the charset is valid (see Charset).
func CheckDigit(v []byte) byte {
	total := 0
	for i, w := range weights {
		if w == 0 {
			continue
		}
		total += int(translit[v[i]]) * w
	}
	r := total % 11
	if r == 10 {
		return 'X'
	}
	return byte('0' + r)
}

// Charset reports whether v is 17 chars drawn from the legal alphabet.
func Charset(v []byte) bool {
	if len(v) != 17 {
		return false
	}
	for _, c := range v {
		if translit[c] < 0 {
			return false
		}
	}
	return true
}

// IsValid reports whether v has a valid charset and check digit.
func IsValid(v string) bool {
	b := []byte(v)
	return Charset(b) && CheckDigit(b) == b[8]
}

// MakeValid returns the VIN with a corrected check digit. Input is upper-cased.
func MakeValid(prefix17 string) (string, error) {
	b := []byte(prefix17)
	for i, c := range b {
		if c >= 'a' && c <= 'z' {
			b[i] = c - 'a' + 'A'
		}
	}
	if !Charset(b) {
		return "", ErrCharset
	}
	b[8] = CheckDigit(b)
	return string(b), nil
}

// Synthetic mirrors seed.py:vin_for — 8 random chars, "0", 2 random chars, 6-digit index.
func Synthetic(i int, rng *rand.Rand) string {
	var b [17]byte
	for k := 0; k < 8; k++ {
		b[k] = Chars[rng.IntN(len(Chars))]
	}
	b[8] = '0'
	b[9] = Chars[rng.IntN(len(Chars))]
	b[10] = Chars[rng.IntN(len(Chars))]
	n := i % 1_000_000
	for k := 16; k >= 11; k-- {
		b[k] = byte('0' + n%10)
		n /= 10
	}
	b[8] = CheckDigit(b[:])
	return string(b[:])
}
