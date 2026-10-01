import { create } from 'zustand';

interface UiStore {
  selectedVin: string | null;
  fleetId: number | null;
  openVehicle: (vin: string) => void;
  closeVehicle: () => void;
  setFleet: (id: number | null) => void;
}

export const useUiStore = create<UiStore>((set) => ({
  selectedVin: null,
  fleetId: null,
  openVehicle: (vin) => set({ selectedVin: vin }),
  closeVehicle: () => set({ selectedVin: null }),
  setFleet: (fleetId) => set({ fleetId }),
}));
