/**
 * Hand-written types from contracts/api.md (REST + WebSocket contract v1).
 */

export type Role = 'admin' | 'manager' | 'finance' | 'auditor';

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
  trace_id?: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface DevTokenRequest {
  tenant_id: number;
  role: Role;
  sub?: string;
}

export interface DevTokenResponse {
  access_token: string;
  expires_in: number;
}

export interface Me {
  sub: string;
  tenant_id: number;
  role: Role;
}

export interface Fleet {
  id: number;
  name: string;
  depot_id: number;
  vehicle_count: number;
}

export interface FleetSummary {
  vehicles: number;
  online: number;
  charging: number;
  at_risk: number;
  avg_soc: number;
  open_alerts: number;
  kwh_today: number;
  cost_today: number;
  savings_today: number;
}

export type VehicleStatus = 'DRIVING' | 'IDLE' | 'CHARGING' | 'OFFLINE' | 'AT_RISK';
export type ChargingState = 'NONE' | 'AC' | 'DC' | 'DONE' | 'INTERRUPTED';

export interface VehicleState {
  vin: string;
  tenant_id: number;
  ts: string;
  seq: number;
  lat: number;
  lon: number;
  speed_kmh: number;
  soc_pct: number;
  soh_pct: number;
  odo_km: number;
  charging_state: ChargingState | string;
  charging_kw: number;
  dtc: string[];
  status: VehicleStatus;
}

export interface Vehicle {
  id: number;
  vin: string;
  plate: string;
  fleet_id: number;
  model: string;
  powertrain: string;
  state: VehicleState | null;
}

export interface TelemetryRow {
  ts: string;
  seq: number;
  lat: number;
  lon: number;
  speed_kmh: number;
  soc_pct: number;
  soh_pct: number;
  odo_km: number;
  charging_kw: number;
}

export interface NearestCharger {
  charger_id: number;
  name: string;
  lat: number;
  lon: number;
  max_kw: number;
  distance_km: number;
  reachable: boolean;
  price_per_kwh: number;
}

export interface RangeForecast {
  range_km: number;
  method: string;
  confidence: number;
}

export type PlanStatus = 'PENDING' | 'PLANNED' | 'ACTIVE' | 'DONE' | 'FAILED' | string;

export interface PlanRequest {
  vin: string;
  target_soc: number;
  depart_ts: string;
}

export interface PlanAccepted {
  plan_id: number | string;
  status: 'PENDING';
}

export interface PlanSlot {
  charger_id: number;
  start_ts: string;
  end_ts: string;
  kw: number;
  kwh: number;
  cost: number;
  price_per_kwh: number;
}

export interface PlanSummary {
  id: number | string;
  vin: string;
  status: PlanStatus;
  target_soc: number;
  depart_ts: string;
  total_cost: number;
  baseline_cost: number;
  savings: number;
}

export interface Plan extends PlanSummary {
  slots: PlanSlot[];
}

export type AlertSeverity = 'CRITICAL' | 'WARN' | 'INFO';
export type AlertType =
  | 'STRAND_RISK'
  | 'LOW_SOC'
  | 'SOC_DROP'
  | 'SOH_DROP'
  | 'BATTERY_DTC'
  | 'CHARGE_INTERRUPTED'
  | 'OFF_PLAN';

export interface Alert {
  id: number | string;
  vin: string;
  type: AlertType | string;
  severity: AlertSeverity;
  ts: string;
  payload: Record<string, unknown>;
  acked_by: string | null;
  acked_ts: string | null;
}

export interface AlertAck {
  id: number | string;
  acked_by: string;
  acked_ts: string;
}

export interface Charger {
  id: number;
  name: string;
  depot_id: number | null;
  public: boolean;
  lat: number;
  lon: number;
  max_kw: number;
  connectors?: number;
  price_per_kwh?: number;
}

export interface TariffSlot {
  dow: number;
  start_min: number;
  end_min: number;
  price_per_kwh: number;
}

export interface Tariff {
  tariff_id: number | string;
  name: string;
  slots: TariffSlot[];
}

export interface CostDay {
  date: string;
  kwh: number;
  cost: number;
  baseline_cost: number;
  savings: number;
  peak_kw: number;
}

export interface CostReport {
  days: CostDay[];
}

export interface BatteryHealthRow {
  vin: string;
  soh_pct: number;
  cycles: number;
  trend_30d: number;
  similar: string[];
}

export type AgentActionStatus =
  'PENDING' | 'APPROVED' | 'REJECTED' | 'EXECUTED' | 'FAILED' | string;

export interface AgentAction {
  id: number | string;
  action: string;
  params: Record<string, unknown>;
  rationale: string;
  status: AgentActionStatus;
}

export interface AgentUsage {
  model?: string | null;
  llm_calls: number;
  tool_calls: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd: number;
  cost_cap_usd?: number;
  latency_ms: number;
  fallback_reason?: string | null;
  stopped_reason?: string | null;
}

export interface AgentAnswer {
  answer: string;
  actions: AgentAction[];
  audit_id: number | string;
  /** "llm" when ANTHROPIC_API_KEY is configured, otherwise the deterministic "offline" router. */
  mode?: 'llm' | 'offline';
  usage?: AgentUsage;
}

export interface AgentDecision {
  id: number | string;
  status: AgentActionStatus;
  /** Present when approving a REPLAN: the plans it created. */
  execution?: { status: 'EXECUTED' | 'FAILED'; plan_ids: (number | string)[]; errors: string[] };
}

/** audit_log row (contracts/api.md GET /audit) */
export interface AuditRow {
  id: number | string;
  ts: string;
  actor_type?: 'user' | 'agent' | 'system';
  actor_id: string;
  action: string;
  resource?: string | null;
  details?: Record<string, unknown> | string | null;
}

/** WebSocket frames */
export type WsMessage =
  | { type: 'state'; data: VehicleState }
  | { type: 'alert'; data: Alert }
  | { type: 'plan'; data: { plan_id: number | string; vin: string; status: PlanStatus } }
  | { type: 'summary'; data: FleetSummary };

export type WsSubscribe =
  { type: 'subscribe'; fleet_id: number } | { type: 'subscribe'; vins: string[] };
