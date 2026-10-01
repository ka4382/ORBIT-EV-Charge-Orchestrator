package vin

import (
	"math/rand/v2"
	"testing"
)

func TestKnownVINs(t *testing.T) {
	// Sample VIN used throughout contracts/oem-formats.md; check digit is '3'.
	for _, v := range []string{"1HGCM82633A004352", "11111111111111111", "1M8GDM9AXKP042788"} {
		if !IsValid(v) {
			t.Errorf("%s should be valid", v)
		}
	}
	if IsValid("1HGCM82634A004352") { // wrong check digit
		t.Error("wrong check digit accepted")
	}
	if IsValid("1HGCM8263IA004352") { // illegal letter I
		t.Error("illegal letter accepted")
	}
	if IsValid("1HGCM82633A00435") { // too short
		t.Error("short VIN accepted")
	}
}

func TestMakeValid(t *testing.T) {
	v, err := MakeValid("1hgcm82603a004352")
	if err != nil || v != "1HGCM82633A004352" {
		t.Fatalf("MakeValid = %q, %v", v, err)
	}
	if _, err := MakeValid("1HGCM8263OA004352"); err == nil {
		t.Error("expected charset error for 'O'")
	}
}

func TestCheckDigitX(t *testing.T) {
	// 1M8GDM9AXKP042788 is the canonical example with check digit X.
	b := []byte("1M8GDM9A0KP042788")
	if got := CheckDigit(b); got != 'X' {
		t.Errorf("want X got %c", got)
	}
}

func TestSyntheticAlwaysValid(t *testing.T) {
	rng := rand.New(rand.NewPCG(1, 2))
	seen := map[string]bool{}
	for i := 1; i <= 20000; i++ {
		v := Synthetic(i, rng)
		if !IsValid(v) {
			t.Fatalf("synthetic VIN %s invalid", v)
		}
		if seen[v] {
			t.Fatalf("duplicate synthetic VIN %s", v)
		}
		seen[v] = true
		if v[11:] != padded(i) {
			t.Fatalf("suffix %s != %s", v[11:], padded(i))
		}
	}
}

func padded(i int) string {
	s := ""
	for k := 0; k < 6; k++ {
		s = string(rune('0'+i%10)) + s
		i /= 10
	}
	return s
}
