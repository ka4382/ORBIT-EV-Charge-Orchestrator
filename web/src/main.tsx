import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { getSession, setSession } from './api/auth';
import { isMock } from './mock';
import './styles/global.css';

async function boot() {
  if (isMock) {
    const { installMocks } = await import('./mock/install');
    installMocks();
    if (!getSession()) {
      setSession({ token: 'mock-token', tenant_id: 12, role: 'manager', sub: 'ops@fleet' });
    }
  }
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void boot();
