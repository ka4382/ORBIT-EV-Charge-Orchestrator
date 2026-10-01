package fleet

import (
	"context"
	"testing"

	"github.com/orbit-fleet/simulator/internal/oem"
	"github.com/orbit-fleet/simulator/internal/vin"
)

func TestSyntheticFleet(t *testing.T) {
	vs := Synthetic(1003, 8, 42)
	if len(vs) != 1003 {
		t.Fatalf("got %d vehicles", len(vs))
	}
	seen := map[[17]byte]bool{}
	var a, b, ice int
	for _, v := range vs {
		if !vin.IsValid(string(v.VIN[:])) {
			t.Fatalf("invalid VIN %s", v.VIN[:])
		}
		if seen[v.VIN] {
			t.Fatalf("duplicate VIN %s", v.VIN[:])
		}
		seen[v.VIN] = true
		if v.OEM == oem.A {
			a++
			if v.TenantID%2 != 1 {
				t.Fatal("OEM_A must be odd tenants")
			}
		} else {
			b++
		}
		if v.Model.Powertrain == ICE {
			ice++
		}
		if v.Depot.Lat == 0 || v.Depot.Lon == 0 {
			t.Fatal("depot unset")
		}
	}
	if a == 0 || b == 0 || ice == 0 || ice > 100 {
		t.Fatalf("unexpected mix a=%d b=%d ice=%d", a, b, ice)
	}
}

func TestLoadFromDBUnreachable(t *testing.T) {
	if _, err := LoadFromDB(context.Background(), "postgres://nobody:none@127.0.0.1:1/none?connect_timeout=1", 10); err == nil {
		t.Fatal("expected error for unreachable database")
	}
}
