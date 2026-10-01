package config

import "testing"

func TestDefaultsAndOverrides(t *testing.T) {
	t.Setenv("SIM_VEHICLES", "123")
	t.Setenv("SIM_TRANSPORT", "STDOUT")
	t.Setenv("SIM_SHARDS", "1000")
	c, err := FromEnv()
	if err != nil {
		t.Fatal(err)
	}
	if c.Vehicles != 123 || c.Transport != "stdout" || c.RateHz != 1 || c.Shards != 123 {
		t.Fatalf("unexpected config %+v", c)
	}
	t.Setenv("SIM_TRANSPORT", "carrier-pigeon")
	if _, err := FromEnv(); err == nil {
		t.Fatal("expected error for bad transport")
	}
}
