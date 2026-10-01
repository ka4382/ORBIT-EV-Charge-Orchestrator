package oem

import "strconv"

const (
	kmPerMile = 1.609344
)

func appendVIN(dst []byte, e *Event, m Malformed) []byte {
	if m != BadVIN {
		return append(dst, e.VIN[:]...)
	}
	var v [17]byte
	copy(v[:], e.VIN[:])
	// Rotate the check digit to a different legal character.
	switch c := v[8]; {
	case c == 'X':
		v[8] = '0'
	case c == '9':
		v[8] = 'X'
	default:
		v[8] = c + 1
	}
	return append(dst, v[:]...)
}

func appendF(dst []byte, v float64, prec int) []byte {
	return strconv.AppendFloat(dst, v, 'f', prec, 64)
}

// appendISO formats unix millis as 2006-01-02T15:04:05.000Z without time.Time.
func appendISO(dst []byte, ms int64) []byte {
	secs := ms / 1000
	msec := ms % 1000
	if msec < 0 {
		msec += 1000
		secs--
	}
	days := secs / 86400
	rem := secs % 86400
	if rem < 0 {
		rem += 86400
		days--
	}
	// Civil-from-days (Howard Hinnant), days since 1970-01-01.
	z := days + 719468
	era := z / 146097
	if z < 0 && z%146097 != 0 {
		era--
	}
	doe := z - era*146097
	yoe := (doe - doe/1460 + doe/36524 - doe/146096) / 365
	y := yoe + era*400
	doy := doe - (365*yoe + yoe/4 - yoe/100)
	mp := (5*doy + 2) / 153
	d := doy - (153*mp+2)/5 + 1
	mo := mp + 3
	if mp >= 10 {
		mo = mp - 9
	}
	if mo <= 2 {
		y++
	}
	dst = append4(dst, int(y))
	dst = append(dst, '-')
	dst = append2(dst, int(mo))
	dst = append(dst, '-')
	dst = append2(dst, int(d))
	dst = append(dst, 'T')
	dst = append2(dst, int(rem/3600))
	dst = append(dst, ':')
	dst = append2(dst, int(rem%3600/60))
	dst = append(dst, ':')
	dst = append2(dst, int(rem%60))
	dst = append(dst, '.')
	dst = append3(dst, int(msec))
	return append(dst, 'Z')
}

func append2(dst []byte, v int) []byte { return append(dst, byte('0'+v/10), byte('0'+v%10)) }
func append3(dst []byte, v int) []byte {
	return append(dst, byte('0'+v/100), byte('0'+v/10%10), byte('0'+v%10))
}
func append4(dst []byte, v int) []byte {
	return append(dst, byte('0'+v/1000), byte('0'+v/100%10), byte('0'+v/10%10), byte('0'+v%10))
}

// EncodeA writes the OEM_A flat payload:
//
//	{"oem":"OEM_A","vin":...,"ts":"...Z","seq":n,"tenant":t,"lat":..,"lon":..,"speed_kmh":..,
//	 "soc":..,"soh":..,"odo_km":..,"amb_c":..,"dtc":[..],"evt":..,"chg":{"st":"NONE","kw":0}}
//
// ICE vehicles send "fuel" instead of "soc"/"soh"; PHEVs send both.
func EncodeA(dst []byte, e *Event, m Malformed) []byte {
	dst = append(dst, `{"oem":"OEM_A","vin":"`...)
	dst = appendVIN(dst, e, m)
	dst = append(dst, `","ts":"`...)
	dst = appendISO(dst, e.TsMs)
	dst = append(dst, `","seq":`...)
	dst = strconv.AppendUint(dst, e.Seq, 10)
	dst = append(dst, `,"tenant":`...)
	dst = strconv.AppendInt(dst, int64(e.TenantID), 10)
	if m != MissingField {
		dst = append(dst, `,"lat":`...)
		dst = appendF(dst, e.Lat, 5)
	}
	dst = append(dst, `,"lon":`...)
	dst = appendF(dst, e.Lon, 5)
	dst = append(dst, `,"speed_kmh":`...)
	dst = appendF(dst, e.SpeedKmh, 1)
	if e.HasSoc {
		dst = append(dst, `,"soc":`...)
		dst = appendF(dst, e.Soc, 1)
		dst = append(dst, `,"soh":`...)
		dst = appendF(dst, e.Soh, 2)
	}
	if e.HasFuel {
		dst = append(dst, `,"fuel":`...)
		dst = appendF(dst, e.FuelPct, 1)
	}
	dst = append(dst, `,"odo_km":`...)
	dst = appendF(dst, e.OdoKm, 1)
	dst = append(dst, `,"amb_c":`...)
	dst = appendF(dst, e.AmbientC, 1)
	dst = append(dst, `,"dtc":[`...)
	for i := 0; i < int(e.NDTC) && i < MaxDTC; i++ {
		if i > 0 {
			dst = append(dst, ',')
		}
		dst = append(dst, '"')
		dst = append(dst, e.DTC[i]...)
		dst = append(dst, '"')
	}
	dst = append(dst, `],"evt":`...)
	if e.Evt == EvtNone {
		dst = append(dst, `null`...)
	} else {
		dst = append(dst, '"')
		dst = append(dst, evtNamesA[e.Evt]...)
		dst = append(dst, '"')
	}
	dst = append(dst, `,"chg":{"st":"`...)
	dst = append(dst, chargeNamesA[e.ChargeSt]...)
	dst = append(dst, `","kw":`...)
	dst = appendF(dst, e.ChargeKw, 1)
	return append(dst, `}}`...)
}

// EncodeB writes the OEM_B nested payload:
//
//	{"oem":"OEM_B","vehicle":{"vin":..,"tenant_id":t},"t":ms,"n":seq,
//	 "pos":{"latitude":..,"longitude":..},"velocity_mph":..,
//	 "battery":{"level":0.41,"health":0.935,"charge_mode":0,"charge_kw":0},
//	 "odometer_mi":..,"temp_f":..,"faults":"P0301;","event_code":"HB"}
//
// ICE vehicles omit "battery" and send "fuel_level" (fraction) instead.
func EncodeB(dst []byte, e *Event, m Malformed) []byte {
	dst = append(dst, `{"oem":"OEM_B","vehicle":{"vin":"`...)
	dst = appendVIN(dst, e, m)
	dst = append(dst, `","tenant_id":`...)
	dst = strconv.AppendInt(dst, int64(e.TenantID), 10)
	dst = append(dst, `},"t":`...)
	dst = strconv.AppendInt(dst, e.TsMs, 10)
	dst = append(dst, `,"n":`...)
	dst = strconv.AppendUint(dst, e.Seq, 10)
	if m != MissingField {
		dst = append(dst, `,"pos":{"latitude":`...)
		dst = appendF(dst, e.Lat, 5)
		dst = append(dst, `,"longitude":`...)
		dst = appendF(dst, e.Lon, 5)
		dst = append(dst, '}')
	}
	dst = append(dst, `,"velocity_mph":`...)
	dst = appendF(dst, e.SpeedKmh/kmPerMile, 1)
	if e.HasSoc {
		dst = append(dst, `,"battery":{"level":`...)
		dst = appendF(dst, e.Soc/100, 4)
		dst = append(dst, `,"health":`...)
		dst = appendF(dst, e.Soh/100, 4)
		dst = append(dst, `,"charge_mode":`...)
		dst = append(dst, byte('0'+e.ChargeSt))
		dst = append(dst, `,"charge_kw":`...)
		dst = appendF(dst, e.ChargeKw, 1)
		dst = append(dst, '}')
	}
	if e.HasFuel {
		dst = append(dst, `,"fuel_level":`...)
		dst = appendF(dst, e.FuelPct/100, 4)
	}
	dst = append(dst, `,"odometer_mi":`...)
	dst = appendF(dst, e.OdoKm/kmPerMile, 1)
	dst = append(dst, `,"temp_f":`...)
	dst = appendF(dst, e.AmbientC*9/5+32, 1)
	dst = append(dst, `,"faults":"`...)
	for i := 0; i < int(e.NDTC) && i < MaxDTC; i++ {
		dst = append(dst, e.DTC[i]...)
		dst = append(dst, ';')
	}
	dst = append(dst, `","event_code":`...)
	if e.Evt == EvtNone {
		dst = append(dst, `null`...)
	} else {
		dst = append(dst, '"')
		dst = append(dst, evtNamesB[e.Evt]...)
		dst = append(dst, '"')
	}
	return append(dst, '}')
}
