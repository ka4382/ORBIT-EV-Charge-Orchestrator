# Simulated OEM payload formats (raw, before normalisation)

Two OEM formats are simulated so the adapter layer is real. Both arrive on `telemetry.raw` as JSON with a
header field `oem` that selects the adapter. Unknown fields are kept in `extras`; unknown `oem` goes to DLQ.

## OEM_A (flat, metric, ISO timestamps)
```json
{"oem":"OEM_A","vin":"1HGCM82633A004352","ts":"2026-09-25T10:15:02.120Z","seq":88412,"tenant":12,
 "lat":21.1702,"lon":72.8311,"speed_kmh":64.2,"soc":41.0,"soh":93.5,"odo_km":18234.7,"amb_c":31.2,
 "dtc":["P0301"],"evt":"HARSH_BRAKE","chg":{"st":"NONE","kw":0}}
```

## OEM_B (nested, imperial, epoch millis, battery as fraction)
```json
{"oem":"OEM_B","vehicle":{"vin":"1HGCM82633A004352","tenant_id":12},"t":1790331302120,"n":88412,
 "pos":{"latitude":21.1702,"longitude":72.8311},"velocity_mph":39.9,
 "battery":{"level":0.41,"health":0.935,"charge_mode":0,"charge_kw":0},
 "odometer_mi":11330.5,"temp_f":88.2,"faults":"P0301;","event_code":"HB"}
```
Mappings: mph→kmh ×1.609344; mi→km ×1.609344; °F→°C (f−32)×5/9; level/health ×100; charge_mode 0/1/2 → NONE/AC/DC;
faults split on ';' ; event_code HB→HARSH_BRAKE, CS→CHARGE_START, CE→CHARGE_STOP, TS→TRIP_START, TE→TRIP_END, DT→DTC_SET.
