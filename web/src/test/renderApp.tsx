import { render } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AppRoutes } from '../App';
import { ToastProvider } from '../components/Toast';
import { makeQueryClient } from '../lib/queryClient';
import { setSession } from '../api/auth';
import { installMocks } from '../mock/install';
import { useFleetStore } from '../ws/store';

export function renderApp(path: string, { auth = true } = {}) {
  installMocks();
  useFleetStore.getState().clear();
  setSession(
    auth ? { token: 'mock-token', tenant_id: 12, role: 'manager', sub: 'ops@fleet' } : null,
  );
  const client = makeQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}
