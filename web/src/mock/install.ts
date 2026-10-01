import { setSocketFactory } from '../ws/useFleetSocket';
import { FakeFleetSocket } from './fakeSocket';
import { installMockFetch } from './mockFetch';

let installed = false;
export function installMocks() {
  if (installed) return;
  installed = true;
  installMockFetch();
  setSocketFactory((url) => new FakeFleetSocket(url));
}
