// Package oem encodes the vehicle snapshot into the two simulated OEM wire formats
// defined in contracts/oem-formats.md. Encoders are hand-written appenders: no
// reflection, no per-call allocation beyond growing the caller's buffer.
package oem

// Kind selects the payload format.
type Kind uint8

const (
	// A is OEM_A: flat, metric, ISO timestamps.
	A Kind = iota
	// B is OEM_B: nested, imperial, epoch millis, battery as fraction.
	B
)

// String returns the OEM code as it appears on the wire.
func (k Kind) String() string {
	if k == B {
		return "OEM_B"
	}
	return "OEM_A"
}

// Evt is the optional event attached to a sample.
type Evt uint8

// Event codes; the numeric zero value means "no event".
const (
	EvtNone Evt = iota
	EvtHarshBrake
	EvtChargeStart
	EvtChargeStop
	EvtTripStart
	EvtTripEnd
	EvtDtcSet
)

var evtNamesA = [...]string{"", "HARSH_BRAKE", "CHARGE_START", "CHARGE_STOP", "TRIP_START", "TRIP_END", "DTC_SET"}
var evtNamesB = [...]string{"", "HB", "CS", "CE", "TS", "TE", "DT"}

// ChargeState mirrors the canonical enum NONE/AC/DC (OEM_B sends it as 0/1/2).
type ChargeState uint8

// Charging states.
const (
	ChargeNone ChargeState = iota
	ChargeAC
	ChargeDC
)

var chargeNamesA = [...]string{"NONE", "AC", "DC"}

// MaxDTC is the maximum number of simultaneously active DTCs carried by an event.
const MaxDTC = 3

// Event is the OEM-neutral snapshot produced by the fleet state machine once per tick.
// It is reused across ticks by the owning shard, so it must not retain references.
type Event struct {
	VIN      [17]byte
	TenantID int32
	TsMs     int64
	Seq      uint64
	Lat, Lon float64
	SpeedKmh float64
	HasSoc   bool // false for ICE: soc/soh/charging omitted
	Soc, Soh float64
	HasFuel  bool // ICE and PHEV
	FuelPct  float64
	OdoKm    float64
	AmbientC float64
	NDTC     uint8
	DTC      [MaxDTC]string
	Evt      Evt
	ChargeSt ChargeState
	ChargeKw float64
}

// Malformed selects a deliberate corruption used to exercise the ingest DLQ.
type Malformed uint8

// Corruption kinds.
const (
	// Clean emits a well-formed payload.
	Clean Malformed = iota
	// BadVIN flips the check digit so VIN validation fails.
	BadVIN
	// MissingField drops a required field (lat for OEM_A, pos for OEM_B).
	MissingField
)

// Encode appends the payload for kind k to dst and returns the extended slice.
func Encode(dst []byte, k Kind, e *Event, m Malformed) []byte {
	if k == B {
		return EncodeB(dst, e, m)
	}
	return EncodeA(dst, e, m)
}
