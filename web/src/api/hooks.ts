import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type {
  Alert,
  AlertAck,
  AgentAnswer,
  AuditRow,
  BatteryHealthRow,
  Charger,
  CostReport,
  Fleet,
  FleetSummary,
  NearestCharger,
  Page,
  Plan,
  PlanAccepted,
  PlanRequest,
  PlanSummary,
  RangeForecast,
  Tariff,
  Vehicle,
  AgentDecision,
} from './types';

export const qk = {
  fleets: ['fleets'] as const,
  summary: ['fleet', 'summary'] as const,
  vehicle: (vin: string) => ['vehicle', vin] as const,
  nearest: (vin: string) => ['vehicle', vin, 'nearest'] as const,
  range: (vin: string) => ['vehicle', vin, 'range'] as const,
  plans: (vin: string) => ['plans', vin] as const,
  plan: (id: string | number) => ['plan', String(id)] as const,
  allPlans: ['plans', 'all'] as const,
  chargers: ['chargers'] as const,
  tariff: ['tariff'] as const,
  cost: (from: string, to: string) => ['reports', 'cost', from, to] as const,
  battery: ['reports', 'battery'] as const,
  audit: ['audit'] as const,
};

export function useFleets() {
  return useQuery({ queryKey: qk.fleets, queryFn: () => api.get<{ items: Fleet[] }>('/fleets') });
}

export function useFleetSummary() {
  return useQuery({
    queryKey: qk.summary,
    queryFn: () => api.get<FleetSummary>('/fleet/summary'),
    refetchInterval: 15_000,
  });
}

export function useVehicle(vin: string | null) {
  return useQuery({
    queryKey: qk.vehicle(vin ?? ''),
    queryFn: () => api.get<Vehicle>(`/vehicles/${encodeURIComponent(vin!)}`),
    enabled: !!vin,
  });
}

export function useNearestChargers(vin: string | null) {
  return useQuery({
    queryKey: qk.nearest(vin ?? ''),
    queryFn: () =>
      api.get<{ items: NearestCharger[] }>(
        `/vehicles/${encodeURIComponent(vin!)}/nearest-chargers`,
        {
          limit: 5,
        },
      ),
    enabled: !!vin,
  });
}

export function useRangeForecast(vin: string | null) {
  return useQuery({
    queryKey: qk.range(vin ?? ''),
    queryFn: () => api.get<RangeForecast>(`/vehicles/${encodeURIComponent(vin!)}/range-forecast`),
    enabled: !!vin,
  });
}

export function useVehiclePlans(vin: string | null) {
  return useQuery({
    queryKey: qk.plans(vin ?? ''),
    queryFn: () => api.get<Page<PlanSummary>>('/plans', { vin, limit: 5 }),
    enabled: !!vin,
  });
}

export function usePlan(id: string | number | null, poll = false) {
  return useQuery({
    queryKey: qk.plan(id ?? ''),
    queryFn: () => api.get<Plan>(`/plans/${id}`),
    enabled: id !== null && id !== '',
    refetchInterval: (q) => (poll && q.state.data?.status === 'PENDING' ? 2000 : false),
  });
}

export function useAllPlans() {
  return useQuery({
    queryKey: qk.allPlans,
    queryFn: async () => {
      const out: PlanSummary[] = [];
      let cursor: string | null = null;
      for (let i = 0; i < 10; i++) {
        const page: Page<PlanSummary> = await api.get<Page<PlanSummary>>('/plans', {
          limit: 100,
          cursor,
        });
        out.push(...page.items);
        cursor = page.next_cursor;
        if (!cursor) break;
      }
      return out;
    },
  });
}

export function useCreatePlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PlanRequest) => api.post<PlanAccepted>('/plans', body),
    onSuccess: (_d, vars) => {
      void qc.invalidateQueries({ queryKey: qk.plans(vars.vin) });
      void qc.invalidateQueries({ queryKey: qk.allPlans });
    },
  });
}

export function useAckAlert() {
  return useMutation({
    mutationFn: (id: Alert['id']) => api.post<AlertAck>(`/alerts/${id}/ack`),
  });
}

export function useChargers(depot_id?: number) {
  return useQuery({
    queryKey: [...qk.chargers, depot_id ?? 'all'],
    queryFn: () => api.get<Page<Charger>>('/chargers', { depot_id, limit: 100 }),
  });
}

export function useTariff() {
  return useQuery({ queryKey: qk.tariff, queryFn: () => api.get<Tariff>('/tariffs/current') });
}

export function useCostReport(from: string, to: string) {
  return useQuery({
    queryKey: qk.cost(from, to),
    queryFn: () => api.get<CostReport>('/reports/cost', { from, to }),
  });
}

export function useBatteryHealth() {
  return useQuery({
    queryKey: qk.battery,
    queryFn: () => api.get<Page<BatteryHealthRow>>('/reports/battery-health', { limit: 50 }),
  });
}

export function useAgentQuery() {
  return useMutation({
    mutationFn: (question: string) => api.post<AgentAnswer>('/agent/query', { question }),
  });
}

export function useAgentDecision() {
  return useMutation({
    mutationFn: ({ id, decision }: { id: string | number; decision: 'approve' | 'reject' }) =>
      api.post<AgentDecision>(`/agent/actions/${id}/${decision}`),
  });
}

export function useAudit() {
  return useQuery({
    queryKey: qk.audit,
    queryFn: () => api.get<Page<AuditRow>>('/audit', { limit: 50 }),
    retry: false,
  });
}
