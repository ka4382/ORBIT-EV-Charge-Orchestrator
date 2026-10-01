import { useEffect, useRef } from 'react';
import maplibregl, { type Map as MlMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { VehicleState } from '../api/types';
import { useFleetStore } from '../ws/store';
import './FleetMap.css';

export const MAP_STYLE = 'https://tiles.openfreemap.org/styles/dark';
const SRC = 'vehicles';

interface Props {
  center?: [number, number];
  zoom?: number;
  onSelect?: (vin: string) => void;
  selectedVin?: string | null;
}

function toGeoJson(vehicles: Record<string, VehicleState>): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: Object.values(vehicles).map((v) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [v.lon, v.lat] },
      properties: { vin: v.vin, status: v.status, soc: v.soc_pct },
    })),
  };
}

/** Live vehicle map. Vehicles are a GeoJSON circle layer with data-driven colours. */
export function FleetMap({ center = [80.2707, 13.0827], zoom = 11, onSelect, selectedVin }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const ready = useRef(false);
  const vehicles = useFleetStore((s) => s.vehicles);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = new maplibregl.Map({
      container: el.current,
      style: MAP_STYLE,
      center,
      zoom,
      attributionControl: { compact: true },
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
    m.on('load', () => {
      m.addSource(SRC, { type: 'geojson', data: toGeoJson(useFleetStore.getState().vehicles) });
      m.addLayer({
        id: 'vehicles-halo',
        type: 'circle',
        source: SRC,
        paint: {
          'circle-radius': ['case', ['==', ['get', 'status'], 'AT_RISK'], 14, 9],
          'circle-color': [
            'match',
            ['get', 'status'],
            'AT_RISK',
            '#F78522',
            'CHARGING',
            '#FFFFFF',
            'OFFLINE',
            '#627061',
            '#A4C29F',
          ],
          'circle-opacity': 0.18,
        },
      });
      m.addLayer({
        id: 'vehicles',
        type: 'circle',
        source: SRC,
        paint: {
          'circle-radius': 5.5,
          'circle-color': [
            'match',
            ['get', 'status'],
            'AT_RISK',
            '#F78522',
            'CHARGING',
            '#FFFFFF',
            'OFFLINE',
            '#627061',
            '#A4C29F',
          ],
          'circle-stroke-color': '#1A1E1A',
          'circle-stroke-width': 1.5,
        },
      });
      m.on('click', 'vehicles', (e) => {
        const vin = e.features?.[0]?.properties?.vin as string | undefined;
        if (vin) onSelectRef.current?.(vin);
      });
      m.on('mouseenter', 'vehicles', () => (m.getCanvas().style.cursor = 'pointer'));
      m.on('mouseleave', 'vehicles', () => (m.getCanvas().style.cursor = ''));
      ready.current = true;
    });
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
      ready.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready.current) return;
    const src = m.getSource(SRC) as maplibregl.GeoJSONSource | undefined;
    src?.setData(toGeoJson(vehicles));
  }, [vehicles]);

  useEffect(() => {
    const m = map.current;
    if (!m || !selectedVin) return;
    const v = vehicles[selectedVin];
    if (v) m.easeTo({ center: [v.lon, v.lat], duration: 600 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVin]);

  return (
    <div className="fleet-map">
      <div ref={el} className="fleet-map-canvas" role="region" aria-label="Fleet map" />
      <div className="map-legend" aria-hidden="true">
        <span>
          <i style={{ background: 'var(--sage)' }} /> OK
        </span>
        <span>
          <i style={{ background: 'var(--ember)' }} /> At risk
        </span>
        <span>
          <i style={{ background: 'var(--paper)' }} /> Charging
        </span>
        <span>
          <i style={{ background: 'var(--moss)' }} /> Offline
        </span>
      </div>
    </div>
  );
}
