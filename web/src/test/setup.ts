import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
});

// jsdom lacks these; maplibre and ResponsiveContainer expect them.
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(globalThis, 'ResizeObserver', { value: RO, writable: true });
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});
Object.defineProperty(window.URL, 'createObjectURL', {
  value: vi.fn(() => 'blob:mock'),
  writable: true,
});

vi.mock('maplibre-gl', () => {
  class Map {
    on() {
      return this;
    }
    addControl() {
      return this;
    }
    remove() {}
    getSource() {
      return undefined;
    }
    easeTo() {}
    getCanvas() {
      return { style: {} };
    }
  }
  class NavigationControl {}
  return { default: { Map, NavigationControl }, Map, NavigationControl };
});
