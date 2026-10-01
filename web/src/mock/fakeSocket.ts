import type { SocketLike } from '../ws/useFleetSocket';
import { summaryFrom } from './data';
import { world } from './world';

/** A WebSocket-shaped object driven by the mock world's timer. */
export class FakeFleetSocket implements SocketLike {
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  private unsub: (() => void) | null = null;

  constructor(_url: string) {
    setTimeout(() => {
      this.onopen?.({});
      this.onmessage?.({
        data: JSON.stringify({ type: 'summary', data: summaryFrom(world.states) }),
      });
      for (const s of world.states)
        this.onmessage?.({ data: JSON.stringify({ type: 'state', data: s }) });
      this.unsub = world.subscribe((m) => this.onmessage?.({ data: JSON.stringify(m) }));
    }, 150);
  }

  send(_data: string) {
    /* subscriptions are accepted silently */
  }

  close() {
    this.unsub?.();
    this.unsub = null;
    this.onclose?.({});
  }
}
