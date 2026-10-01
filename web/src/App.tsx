import { useEffect } from 'react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import { isAuthenticated } from './api/auth';
import { setUnauthorizedHandler } from './api/client';
import { makeQueryClient } from './lib/queryClient';
import { ToastProvider } from './components/Toast';
import { ConsoleLayout } from './layout/ConsoleLayout';
import { Agent } from './pages/Agent';
import { Alerts } from './pages/Alerts';
import { FleetConsole } from './pages/FleetConsole';
import { Landing } from './pages/Landing';
import { Login } from './pages/Login';
import { Reports } from './pages/Reports';
import { Schedule } from './pages/Schedule';
import { Vehicles } from './pages/Vehicles';

function RequireAuth() {
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    setUnauthorizedHandler(() =>
      navigate('/login', { replace: true, state: { from: location.pathname } }),
    );
    return () => setUnauthorizedHandler(null);
  }, [navigate, location.pathname]);
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth />}>
        <Route path="/console" element={<ConsoleLayout />}>
          <Route index element={<FleetConsole />} />
          <Route path="vehicles" element={<Vehicles />} />
          <Route path="schedule" element={<Schedule />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="reports" element={<Reports />} />
          <Route path="agent" element={<Agent />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App({ client = makeQueryClient() }: { client?: QueryClient }) {
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <BrowserRouter>
          <AppRoutes />
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
