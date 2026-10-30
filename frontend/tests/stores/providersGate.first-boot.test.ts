/**
 * Regression tests for issue #5 — the "Add an API key to get started" gate
 * does not appear on the first clean boot, but does appear after the user
 * visits Settings → Model Providers and returns to Chat.
 *
 * Boot sequence on a clean install (no session cookie, server may still be
 * initialising):
 *   1. App.tsx mount effect → providersGateStore.check() → GET /api/providers
 *      answers 401 (auth enabled, no cookie yet) or 503 (initialising)
 *   2. AuthContext.login succeeds → cookie set, NO page reload
 *   3. Chat view mounts → NonTerminalContent effect → gateProviders()
 *
 * Step 3 must show the gate. Step 1's failure must not be remembered as
 * "configured", and a 503 must be retried instead of treated as an answer.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { gateProviders } from '@/utils/providersGate';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';
import { providersService } from '@/services/api';

vi.mock('@/services/api', () => ({
  providersService: { getProviders: vi.fn() },
}));

vi.mock('@/components/common/DialogHost', () => ({
  showAlertDialog: vi.fn(),
  showConfirmDialog: vi.fn(),
  showPromptDialog: vi.fn(),
}));

const { showAlertDialog } = await import('@/components/common/DialogHost');
const mockedShowAlert = vi.mocked(showAlertDialog);
const mockedGetProviders = vi.mocked(providersService.getProviders);

const httpError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), { status }) as Error & { status: number };

const fakeProviders = (rows: Array<{ has_key: boolean }>) =>
  rows.map((r, i) => ({
    id: `p${i}`,
    display_name: `p${i}`,
    has_key: r.has_key,
    configurable: true,
    is_oauth: false,
    key_source: 'none' as const,
    auth_error: null,
    models: [],
  }));

const unconfigured = {
  providers: fakeProviders([{ has_key: false }]) as never,
  active_provider: null,
};

describe('issue #5 — providers gate on the first clean boot', () => {
  beforeEach(() => {
    useProvidersGateStore.setState({ configured: null, checkedAt: null, lastErrorStatus: null });
    mockedShowAlert.mockReset();
    mockedGetProviders.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('does not remember a pre-login 401, so the gate still fires after login', async () => {
    // Step 1 — App.tsx boot check runs before the session cookie exists.
    mockedGetProviders.mockRejectedValueOnce(httpError(401));
    const boot = await useProvidersGateStore.getState().check();
    expect(boot).toBeNull();
    expect(useProvidersGateStore.getState().configured).toBeNull();

    // Steps 2/3 — user is logged in, chat mounts, the backend is healthy now.
    mockedGetProviders.mockResolvedValueOnce(unconfigured as never);
    mockedShowAlert.mockResolvedValueOnce(undefined);

    await expect(gateProviders()).resolves.toBe('navigated');
    expect(mockedShowAlert).toHaveBeenCalledTimes(1);
  });

  it('retries while the server reports 503 initializing, then gates', async () => {
    vi.useFakeTimers();
    mockedGetProviders
      .mockRejectedValueOnce(httpError(503))
      .mockRejectedValueOnce(httpError(503))
      .mockResolvedValueOnce(unconfigured as never);
    mockedShowAlert.mockResolvedValueOnce(undefined);

    const pending = gateProviders();
    await vi.advanceTimersByTimeAsync(2500);

    await expect(pending).resolves.toBe('navigated');
    expect(mockedGetProviders).toHaveBeenCalledTimes(3);
  });

  it('fails open on a hard error but leaves the state unknown for the next entry', async () => {
    mockedGetProviders.mockRejectedValueOnce(httpError(500));
    await expect(gateProviders()).resolves.toBe('passed');
    expect(mockedShowAlert).not.toHaveBeenCalled();
    expect(useProvidersGateStore.getState().configured).toBeNull();

    mockedGetProviders.mockResolvedValueOnce(unconfigured as never);
    mockedShowAlert.mockResolvedValueOnce(undefined);
    await expect(gateProviders()).resolves.toBe('navigated');
  });

  it('keeps a known "configured" answer when a later check fails', async () => {
    useProvidersGateStore.setState({ configured: true });
    mockedGetProviders.mockRejectedValueOnce(httpError(500));
    await expect(useProvidersGateStore.getState().check()).resolves.toBe(true);
    expect(useProvidersGateStore.getState().configured).toBe(true);
  });
});
