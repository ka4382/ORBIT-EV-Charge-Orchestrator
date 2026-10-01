import type {
  Alert,
  BatteryHealthRow,
  Charger,
  CostDay,
  Fleet,
  FleetSummary,
  NearestCharger,
  Plan,
  PlanSlot,
  Tariff,
  Vehicle,
  VehicleState,
  VehicleStatus,
  AuditRow,
} from '../api/types';

/** Deterministic PRNG so screenshots are reproducible. */
export function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const CHENNAI = { lat: 13.0827, lon: 80.2707 };
export const TENANT_ID = 12;

export const fleets: Fleet[] = [
  { id: 3, name: 'Chennai Metro Delivery', depot_id: 1, vehicle_count: 98_412 },
  { id: 4, name: 'Airport Shuttle', depot_id: 2, vehicle_count: 1_240 },
];

const models = ['A-Van 60', 'A-Sedan 45', 'B-Truck 120', 'B-Hatch 30', 'A-Hybrid'];

const vinChars = 'ABCDEFGHJKLMNPRSTUVWXYZ0123456789';
function mkVin(rng: () => number) {
  let v = 'MAT';
  for (let i = 0; i < 14; i++) v += vinChars[Math.floor(rng() * vinChars.length)];
  return v;
}

export interface SimVehicle {
  vehicle: Vehicle;
  state: VehicleState;
  heading: number;
}

export function makeVehicles(n = 10, seed = 7): SimVehicle[] {
  const rng = mulberry32(seed);
  const out: SimVehicle[] = [];
  const statuses: VehicleStatus[] = [
    'DRIVING',
    'DRIVING',
    'CHARGING',
    'IDLE',
    'AT_RISK',
    'DRIVING',
    'OFFLINE',
    'DRIVING',
    'CHARGING',
    'AT_RISK',
  ];
  for (let i = 0; i < n; i++) {
    const vin = mkVin(rng);
    const status = statuses[i % statuses.length]!;
    const soc =
      status === 'AT_RISK'
        ? 8 + rng() * 9
        : status === 'CHARGING'
          ? 30 + rng() * 40
          : 25 + rng() * 70;
    const state: VehicleState = {
      vin,
      tenant_id: TENANT_ID,
      ts: new Date(Date.now() - (status === 'OFFLINE' ? 400_000 : 1000)).toISOString(),
      seq: 1000 + i,
      lat: CHENNAI.lat + (rng() - 0.5) * 0.18,
      lon: CHENNAI.lon + (rng() - 0.5) * 0.18,
      speed_kmh: status === 'DRIVING' || status === 'AT_RISK' ? 20 + rng() * 40 : 0,
      soc_pct: Math.round(soc * 10) / 10,
      soh_pct: Math.round((86 + rng() * 12) * 10) / 10,
      odo_km: Math.round(5000 + rng() * 60000),
      charging_state: status === 'CHARGING' ? (rng() > 0.5 ? 'DC' : 'AC') : 'NONE',
      charging_kw: status === 'CHARGING' ? Math.round(7 + rng() * 40) : 0,
      dtc: i === 4 ? ['P0A80'] : i === 9 ? ['P0301', 'P1A00'] : [],
      status,
    };
    out.push({
      vehicle: {
        id: i + 1,
        vin,
        plate: `TN ${String(1 + (i % 9)).padStart(2, '0')} ${['AB', 'BC', 'CD'][i % 3]} ${1000 + i * 137}`,
        fleet_id: i < 8 ? 3 : 4,
        model: models[i % models.length]!,
        powertrain: 'BEV',
        state,
      },
      state,
      heading: rng() * Math.PI * 2,
    });
  }
  return out;
}

export const chargers: Charger[] = [
  {
    id: 1,
    name: 'Depot A · Bay 1',
    depot_id: 1,
    public: false,
    lat: 13.06,
    lon: 80.24,
    max_kw: 60,
  },
  {
    id: 2,
    name: 'Depot A · Bay 2',
    depot_id: 1,
    public: false,
    lat: 13.06,
    lon: 80.241,
    max_kw: 60,
  },
  {
    id: 3,
    name: 'Depot A · Bay 3',
    depot_id: 1,
    public: false,
    lat: 13.06,
    lon: 80.242,
    max_kw: 22,
  },
  {
    id: 4,
    name: 'Depot A · Bay 4',
    depot_id: 1,
    public: false,
    lat: 13.06,
    lon: 80.243,
    max_kw: 22,
  },
  {
    id: 5,
    name: 'Guindy Public DC',
    depot_id: null,
    public: true,
    lat: 13.01,
    lon: 80.21,
    max_kw: 120,
  },
  {
    id: 6,
    name: 'OMR Fast Hub',
    depot_id: null,
    public: true,
    lat: 12.95,
    lon: 80.24,
    max_kw: 150,
  },
];

export function nearestFor(state: VehicleState): NearestCharger[] {
  const range = state.soc_pct * 2.6;
  return chargers
    .map((c) => {
      const d = haversine(state.lat, state.lon, c.lat, c.lon);
      return {
        charger_id: c.id,
        name: c.name,
        lat: c.lat,
        lon: c.lon,
        max_kw: c.max_kw,
        distance_km: Math.round(d * 10) / 10,
        reachable: d < range * 0.8,
        price_per_kwh: c.public ? 18 : 7.5,
      };
    })
    .sort((a, b) => a.distance_km - b.distance_km)
    .slice(0, 5);
}

export function haversine(lat1: number, lon1: number, lat2: number, lon2: number) {
  const R = 6371;
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** Time-of-use tariff, INR/kWh: off-peak 00-06 & 22-24, peak 18-22, normal otherwise. */
export const tariff: Tariff = {
  tariff_id: 1,
  name: 'TN ToU commercial',
  slots: Array.from({ length: 7 }, (_, dow) => [
    { dow, start_min: 0, end_min: 360, price_per_kwh: 5.5 },
    { dow, start_min: 360, end_min: 1080, price_per_kwh: 8.0 },
    { dow, start_min: 1080, end_min: 1320, price_per_kwh: 12.5 },
    { dow, start_min: 1320, end_min: 1440, price_per_kwh: 5.5 },
  ]).flat(),
};

function todayAt(h: number, m = 0) {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d;
}

function slotsFor(
  chargerId: number,
  startH: number,
  startM: number,
  count: number,
  kw: number,
): PlanSlot[] {
  const out: PlanSlot[] = [];
  for (let i = 0; i < count; i++) {
    const s = new Date(todayAt(startH, startM).getTime() + i * 15 * 60_000);
    const e = new Date(s.getTime() + 15 * 60_000);
    const min = s.getHours() * 60 + s.getMinutes();
    const price = min < 360 || min >= 1320 ? 5.5 : min >= 1080 ? 12.5 : 8.0;
    const kwh = kw / 4;
    out.push({
      charger_id: chargerId,
      start_ts: s.toISOString(),
      end_ts: e.toISOString(),
      kw,
      kwh,
      cost: Math.round(kwh * price * 100) / 100,
      price_per_kwh: price,
    });
  }
  return out;
}

export function makePlans(vins: string[]): Plan[] {
  const specs: [number, number, number, number, number][] = [
    [1, 1, 0, 12, 60],
    [2, 2, 30, 8, 60],
    [3, 4, 15, 10, 22],
    [1, 22, 0, 8, 60],
    [4, 13, 0, 6, 22],
    [2, 23, 0, 4, 60],
  ];
  return specs.map(([charger, h, m, n, kw], i) => {
    const slots = slotsFor(charger, h, m, n, kw);
    const total = round2(slots.reduce((a, s) => a + s.cost, 0));
    const kwh = slots.reduce((a, s) => a + s.kwh, 0);
    const baseline = round2(kwh * 12.5);
    return {
      id: 100 + i,
      vin: vins[i % vins.length]!,
      status: i === 4 ? 'ACTIVE' : 'PLANNED',
      target_soc: 90,
      depart_ts: todayAt(7 + i, 0).toISOString(),
      total_cost: total,
      baseline_cost: baseline,
      savings: round2(baseline - total),
      slots,
    };
  });
}

export const round2 = (n: number) => Math.round(n * 100) / 100;

export function makeAlerts(vins: string[]): Alert[] {
  const now = Date.now();
  const mk = (
    i: number,
    vin: string,
    type: string,
    severity: Alert['severity'],
    minsAgo: number,
    payload: Record<string, unknown>,
    acked = false,
  ): Alert => ({
    id: 500 + i,
    vin,
    type,
    severity,
    ts: new Date(now - minsAgo * 60_000).toISOString(),
    payload,
    acked_by: acked ? 'ops@fleet' : null,
    acked_ts: acked ? new Date(now - (minsAgo - 3) * 60_000).toISOString() : null,
  });
  return [
    mk(1, vins[4]!, 'STRAND_RISK', 'CRITICAL', 2, {
      soc_pct: 11.2,
      remaining_range_km: 24,
      nominal_range_km: 220,
    }),
    mk(2, vins[9]!, 'BATTERY_DTC', 'CRITICAL', 6, {
      codes: ['P0A80'],
      descriptions: { P0A80: 'Replace hybrid/EV battery pack' },
    }),
    mk(3, vins[0]!, 'LOW_SOC', 'WARN', 14, { soc_pct: 18.4 }),
    mk(
      4,
      vins[2]!,
      'CHARGE_INTERRUPTED',
      'WARN',
      27,
      { soc_pct: 41, prev_state: 'DC', prev_kw: 48, gap_s: 12 },
      true,
    ),
    mk(5, vins[6]!, 'SOH_DROP', 'WARN', 65, { soh_pct: 79.4 }),
    mk(6, vins[1]!, 'OFF_PLAN', 'INFO', 90, { charger_id: 5, price_per_kwh: 12.5 }, true),
    mk(7, vins[3]!, 'SOC_DROP', 'WARN', 140, {
      from_soc_pct: 81,
      to_soc_pct: 38,
      drop_pts: 43,
      window_s: 7200,
    }),
  ];
}

export function summaryFrom(states: VehicleState[]): FleetSummary {
  const online = states.filter((s) => s.status !== 'OFFLINE').length;
  const charging = states.filter((s) => s.status === 'CHARGING').length;
  const at_risk = states.filter((s) => s.status === 'AT_RISK').length;
  const avg = states.reduce((a, s) => a + s.soc_pct, 0) / Math.max(1, states.length);
  const scale = 98_412 / Math.max(1, states.length);
  return {
    vehicles: 98_412,
    online: Math.round(online * scale) - 1_337,
    charging: charging * 1_243 + 17,
    at_risk: at_risk * 71 + 3,
    avg_soc: Math.round(avg * 10) / 10,
    open_alerts: 5,
    kwh_today: 184_320,
    cost_today: 1_327_100,
    savings_today: 331_800,
  };
}

export function makeCostDays(from: string, to: string): CostDay[] {
  const rng = mulberry32(42);
  const out: CostDay[] = [];
  const start = new Date(from);
  const end = new Date(to);
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    const kwh = 150_000 + rng() * 60_000;
    const baseline = kwh * 9.2;
    const cost = baseline * (0.72 + rng() * 0.1);
    out.push({
      date: d.toISOString().slice(0, 10),
      kwh: Math.round(kwh),
      cost: Math.round(cost),
      baseline_cost: Math.round(baseline),
      savings: Math.round(baseline - cost),
      peak_kw: Math.round(3000 + rng() * 1500),
    });
  }
  return out;
}

export function makeBattery(vins: string[]): BatteryHealthRow[] {
  const rng = mulberry32(3);
  return vins.map((vin, i) => ({
    vin,
    soh_pct: round2(84 + rng() * 14),
    cycles: Math.round(200 + rng() * 900),
    trend_30d: round2(-(rng() * 2.5)),
    similar: [vins[(i + 1) % vins.length]!, vins[(i + 3) % vins.length]!],
  }));
}

export function makeAudit(): AuditRow[] {
  const now = Date.now();
  return [
    {
      id: 1,
      ts: new Date(now - 3_600_000).toISOString(),
      actor_type: 'agent',
      actor_id: 'agent',
      action: 'agent.query',
      resource: null,
      details: { question: 'Which vehicles will not make their 7am departure?' },
    },
    {
      id: 2,
      ts: new Date(now - 3_500_000).toISOString(),
      actor_type: 'user',
      actor_id: 'manager@fleet',
      action: 'agent.action.approve',
      resource: 'act-1',
      details: { action: 'reschedule_charge' },
    },
    {
      id: 3,
      ts: new Date(now - 7_200_000).toISOString(),
      actor_type: 'user',
      actor_id: 'manager@fleet',
      action: 'alert.ack',
      resource: '503',
      details: null,
    },
  ];
}
