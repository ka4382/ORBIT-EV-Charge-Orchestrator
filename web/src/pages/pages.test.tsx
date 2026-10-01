import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { renderApp } from '../test/renderApp';
import { world } from '../mock/world';
import { setSession } from '../api/auth';

afterEach(() => {
  world.stop();
  setSession(null);
});

describe('pages in mock mode', () => {
  it('renders the landing page', () => {
    renderApp('/');
    expect(screen.getByRole('heading', { level: 1, name: /orbit/i })).toBeInTheDocument();
    expect(
      screen.getByText('Every vehicle charged, on time, at the lowest tariff.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open console/i })).toHaveAttribute('href', '/console');
    expect(screen.getByText(/synthetic data only/i)).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('renders the login form and mints a dev token', async () => {
    renderApp('/login', { auth: false });
    fireEvent.change(screen.getByLabelText(/tenant id/i), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: /get token/i }));
    await waitFor(() => expect(sessionStorage.getItem('orbit.token')).toMatch(/^eyJ/));
    expect(JSON.parse(sessionStorage.getItem('orbit.session') ?? '{}')).toMatchObject({
      tenant_id: 7,
      role: 'manager',
    });
  });

  it('redirects to /login when unauthenticated', () => {
    renderApp('/console', { auth: false });
    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument();
  });

  it('renders the fleet console with KPIs and the live timeline', async () => {
    renderApp('/console');
    await waitFor(() => expect(screen.getByText('Vehicles online')).toBeInTheDocument());
    expect(screen.getByText('Live events')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: /fleet map/i })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/LIVE · 98,412 vehicles/)).toBeInTheDocument());
  });

  it('renders vehicles, loads more, and opens the drawer', async () => {
    renderApp('/console/vehicles');
    await waitFor(() => expect(screen.getAllByRole('row').length).toBeGreaterThan(2));
    const before = screen.getAllByRole('row').length;
    fireEvent.click(screen.getByRole('button', { name: /load more/i }));
    await waitFor(() => expect(screen.getAllByRole('row').length).toBeGreaterThan(before));
    const firstVin = world.vehicles[0]!.vehicle.vin;
    fireEvent.click(screen.getByText(firstVin));
    const drawer = screen.getByRole('dialog');
    await waitFor(() => expect(within(drawer).getByText(/nearest chargers/i)).toBeInTheDocument());
    await waitFor(() => expect(within(drawer).getAllByText(/Depot A/).length).toBeGreaterThan(0));
    fireEvent.click(within(drawer).getByRole('button', { name: /^plan charge$/i }));
    await waitFor(() => expect(screen.getByText(/Plan #\d+ requested/)).toBeInTheDocument());
  });

  it('renders the depot schedule with totals', async () => {
    renderApp('/console/schedule');
    await waitFor(() => expect(screen.getByText('Planned kWh')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText(/Depot A · Bay 1/).length).toBeGreaterThan(0));
    expect(screen.getByText('Off-peak')).toBeInTheDocument();
  });

  it('renders alerts and acknowledges one', async () => {
    renderApp('/console/alerts');
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /^ack$/i }).length).toBeGreaterThan(0),
    );
    const n = screen.getAllByRole('button', { name: /^ack$/i }).length;
    fireEvent.click(screen.getAllByRole('button', { name: /^ack$/i })[0]!);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /^ack$/i }).length).toBe(n - 1),
    );
    expect(screen.getAllByText('Acked').length).toBeGreaterThan(0);
  });

  it('renders reports with the cost chart and battery table', async () => {
    renderApp('/console/reports');
    await waitFor(() =>
      expect(screen.getByText(/Cost per day vs flat-tariff baseline/)).toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getAllByText(/pts$/).length).toBeGreaterThan(0));
  });

  it('renders the agent, asks a question and approves an action', async () => {
    renderApp('/console/agent');
    fireEvent.change(screen.getByLabelText(/question/i), { target: { value: 'Who is at risk?' } });
    fireEvent.click(screen.getByRole('button', { name: /^ask$/i }));
    await waitFor(() => expect(screen.getByText(/at risk of not making/)).toBeInTheDocument(), {
      timeout: 3000,
    });
    fireEvent.click(screen.getAllByRole('button', { name: /approve/i })[0]!);
    await waitFor(() => expect(screen.getAllByText('APPROVED').length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByText(/agent.action.approve/)).toBeInTheDocument());
  });
});
