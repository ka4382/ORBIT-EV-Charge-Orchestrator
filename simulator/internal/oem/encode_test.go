package oem

import (
	"encoding/json"
	"math"
	"sort"
	"strings"
	"testing"
	"time"
)

func sample() *Event {
	e := &Event{
		TenantID: 12, TsMs: 1758795302120, Seq: 88412,
		Lat: 21.1702, Lon: 72.8311, SpeedKmh: 64.2,
		HasSoc: true, Soc: 41.0, Soh: 93.5, OdoKm: 18234.7, AmbientC: 31.2,
		NDTC: 1, Evt: EvtHarshBrake, ChargeSt: ChargeNone, ChargeKw: 0,
	}
	copy(e.VIN[:], "1HGCM82633A004352")
	e.DTC[0] = "P0301"
	return e
}

func keys(m map[string]any) string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return strings.Join(out, ",")
}

func TestEncodeA_ContractFields(t *testing.T) {
	b := EncodeA(nil, sample(), Clean)
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("invalid JSON %s: %v", b, err)
	}
	want := "amb_c,chg,dtc,evt,lat,lon,odo_km,oem,seq,soc,soh,speed_kmh,tenant,ts,vin"
	if got := keys(m); got != want {
		t.Fatalf("OEM_A keys\n got %s\nwant %s", got, want)
	}
	if m["oem"] != "OEM_A" || m["vin"] != "1HGCM82633A004352" || m["ts"] != "2025-09-25T10:15:02.120Z" {
		t.Fatalf("header fields wrong: %s", b)
	}
	if m["seq"].(float64) != 88412 || m["tenant"].(float64) != 12 {
		t.Fatalf("seq/tenant wrong: %s", b)
	}
	if m["speed_kmh"].(float64) != 64.2 || m["soc"].(float64) != 41 || m["soh"].(float64) != 93.5 {
		t.Fatalf("metrics wrong: %s", b)
	}
	if m["evt"] != "HARSH_BRAKE" || m["dtc"].([]any)[0] != "P0301" {
		t.Fatalf("evt/dtc wrong: %s", b)
	}
	chg := m["chg"].(map[string]any)
	if keys(chg) != "kw,st" || chg["st"] != "NONE" {
		t.Fatalf("chg wrong: %s", b)
	}
	// Timestamp must round-trip through time.Parse with the contract layout.
	ts, err := time.Parse("2006-01-02T15:04:05.000Z", m["ts"].(string))
	if err != nil || ts.UnixMilli() != 1758795302120 {
		t.Fatalf("ts round trip: %v %v", ts, err)
	}
}

func TestEncodeB_ContractFields(t *testing.T) {
	b := EncodeB(nil, sample(), Clean)
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("invalid JSON %s: %v", b, err)
	}
	want := "battery,event_code,faults,n,odometer_mi,oem,pos,t,temp_f,vehicle,velocity_mph"
	if got := keys(m); got != want {
		t.Fatalf("OEM_B keys\n got %s\nwant %s", got, want)
	}
	veh := m["vehicle"].(map[string]any)
	if keys(veh) != "tenant_id,vin" || veh["vin"] != "1HGCM82633A004352" || veh["tenant_id"].(float64) != 12 {
		t.Fatalf("vehicle wrong: %s", b)
	}
	if m["t"].(float64) != 1758795302120 || m["n"].(float64) != 88412 {
		t.Fatalf("t/n wrong: %s", b)
	}
	pos := m["pos"].(map[string]any)
	if keys(pos) != "latitude,longitude" || pos["latitude"].(float64) != 21.1702 {
		t.Fatalf("pos wrong: %s", b)
	}
	bat := m["battery"].(map[string]any)
	if keys(bat) != "charge_kw,charge_mode,health,level" || bat["level"].(float64) != 0.41 || bat["health"].(float64) != 0.935 || bat["charge_mode"].(float64) != 0 {
		t.Fatalf("battery wrong: %s", b)
	}
	if math.Abs(m["velocity_mph"].(float64)-39.9) > 0.05 || math.Abs(m["odometer_mi"].(float64)-11330.5) > 0.05 || math.Abs(m["temp_f"].(float64)-88.2) > 0.05 {
		t.Fatalf("imperial conversions wrong: %s", b)
	}
	if m["faults"] != "P0301;" || m["event_code"] != "HB" {
		t.Fatalf("faults/event_code wrong: %s", b)
	}
}

func TestEncodeICEAndNoEvent(t *testing.T) {
	e := sample()
	e.HasSoc, e.HasFuel, e.FuelPct, e.Evt, e.NDTC = false, true, 55.5, EvtNone, 0
	var a, b map[string]any
	if err := json.Unmarshal(EncodeA(nil, e, Clean), &a); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(EncodeB(nil, e, Clean), &b); err != nil {
		t.Fatal(err)
	}
	if _, ok := a["soc"]; ok {
		t.Fatal("ICE OEM_A must not send soc")
	}
	if a["fuel"].(float64) != 55.5 || a["evt"] != nil || len(a["dtc"].([]any)) != 0 {
		t.Fatalf("OEM_A ICE wrong: %v", a)
	}
	if _, ok := b["battery"]; ok {
		t.Fatal("ICE OEM_B must not send battery")
	}
	if b["fuel_level"].(float64) != 0.555 || b["event_code"] != nil || b["faults"] != "" {
		t.Fatalf("OEM_B ICE wrong: %v", b)
	}
}

func TestEncodeChargingDC(t *testing.T) {
	e := sample()
	e.ChargeSt, e.ChargeKw, e.Evt = ChargeDC, 100, EvtChargeStart
	var a, b map[string]any
	_ = json.Unmarshal(EncodeA(nil, e, Clean), &a)
	_ = json.Unmarshal(EncodeB(nil, e, Clean), &b)
	if a["chg"].(map[string]any)["st"] != "DC" || a["chg"].(map[string]any)["kw"].(float64) != 100 || a["evt"] != "CHARGE_START" {
		t.Fatalf("OEM_A charging wrong: %v", a)
	}
	if b["battery"].(map[string]any)["charge_mode"].(float64) != 2 || b["event_code"] != "CS" {
		t.Fatalf("OEM_B charging wrong: %v", b)
	}
}

func TestMalformed(t *testing.T) {
	e := sample()
	var a, b map[string]any
	_ = json.Unmarshal(EncodeA(nil, e, BadVIN), &a)
	if a["vin"] == "1HGCM82633A004352" || a["vin"].(string)[:8] != "1HGCM826" {
		t.Fatalf("BadVIN should only alter the check digit: %v", a["vin"])
	}
	a = nil
	_ = json.Unmarshal(EncodeA(nil, e, MissingField), &a)
	if _, ok := a["lat"]; ok {
		t.Fatal("MissingField A should drop lat")
	}
	if err := json.Unmarshal(EncodeB(nil, e, MissingField), &b); err != nil {
		t.Fatal(err)
	}
	if _, ok := b["pos"]; ok {
		t.Fatal("MissingField B should drop pos")
	}
}

func TestISOTimestamps(t *testing.T) {
	for _, ms := range []int64{0, 951782400000, 1758795302120, 4102444799999, -1} {
		got := string(appendISO(nil, ms))
		want := time.UnixMilli(ms).UTC().Format("2006-01-02T15:04:05.000Z")
		if got != want {
			t.Errorf("ms %d: got %s want %s", ms, got, want)
		}
	}
}

func BenchmarkEncodeA(b *testing.B) {
	e := sample()
	buf := make([]byte, 0, 512)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		buf = EncodeA(buf[:0], e, Clean)
	}
	b.SetBytes(int64(len(buf)))
}

func BenchmarkEncodeB(b *testing.B) {
	e := sample()
	buf := make([]byte, 0, 512)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		buf = EncodeB(buf[:0], e, Clean)
	}
	b.SetBytes(int64(len(buf)))
}
