// k6 + xk6-kafka: push raw OEM_A events straight into telemetry.raw at a target rate to measure ingest capacity
// independent of the Go simulator. Build: xk6 build --with github.com/mostafa/xk6-kafka@latest
import { Writer, SchemaRegistry, SCHEMA_TYPE_JSON } from "k6/x/kafka";
import { check } from "k6";

const writer = new Writer({ brokers: [__ENV.KAFKA_BOOTSTRAP || "localhost:19092"], topic: "telemetry.raw", compression: "lz4", batchSize: 1000 });
const registry = new SchemaRegistry();
const VINS = ["1HGCM82633A004352", "JH4KA7561PC008269", "1FTFW1ET5DKE12345"];

export const options = {
  scenarios: { ingest: { executor: "constant-arrival-rate", rate: Number(__ENV.RATE || 20000), timeUnit: "1s", duration: __ENV.DURATION || "60s", preAllocatedVUs: 50, maxVUs: 400 } },
  thresholds: { kafka_writer_error_count: ["count==0"] },
};

let seq = 0;
export default function () {
  const msgs = [];
  for (let i = 0; i < 50; i++) {
    seq++;
    const vin = VINS[seq % VINS.length];
    msgs.push({ key: registry.serialize({ data: vin, schemaType: SCHEMA_TYPE_JSON }),
      value: registry.serialize({ data: { oem: "OEM_A", vin, ts: new Date().toISOString(), seq, tenant: 1, lat: 13.08, lon: 80.27, speed_kmh: 40, soc: 55, soh: 95, odo_km: 1000 + seq, amb_c: 30, dtc: [], evt: null, chg: { st: "NONE", kw: 0 } }, schemaType: SCHEMA_TYPE_JSON }) });
  }
  writer.produce({ messages: msgs });
}
export function teardown() { writer.close(); }
