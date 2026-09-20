/**
 * SettingsDetail providers-row refresh behavior:
 *  - After a successful providersService.setKey(), the gate flips to true
 *    based on the fresh provider list.
 *  - After a successful providersService.deleteKey(), the gate flips to
 *    false based on the fresh provider list.
 *  - If the post-mutation getProviders() throws, handleSave fails OPEN
 *    (user just added a key) and handleDelete fails CLOSED (user just
 *    removed one). This guards against R1's fail-open side effect
 *    leaving the store stuck on a stale value.
 *  - ProvidersGroup on mount syncs the gate from the initial fetch,
 *    correcting any stale state.
 *
 * The test exercises ProviderRow through the visible SettingsDetail
 * component, since ProviderRow is not exported directly.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, fireEvent, cleanup, waitFor } from '@testing-library/react';
import SettingsDetail from '@/components/settings/SettingsDetail';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { providersService } from '@/services/api';

vi.mock('@/services/api', () => ({
  providersService: {
    getProviders: vi.fn(),
    setKey: vi.fn(),
    deleteKey: vi.fn(),
  },
  configFileService: {
    getRaw: vi.fn().mockResolvedValue(''),
    saveRaw: vi.fn(),
  },
}));

vi.mock('@/stores/useSettingsStore', () => {
  const useSettingsStore = (sel?: (s: Record<string, unknown>) => unknown) =>
    sel ? sel({ settings: null, loadSettings: () => {} }) : { settings: null, loadSettings: () => {} };
  (useSettingsStore as unknown as { getState: () => Record<string, unknown> }).getState =
    () => ({ settings: null, loadSettings: () => {} });
  return { useSettingsStore };
});

const mockedGetProviders = vi.mocked(providersService.getProviders);
const mockedSetKey = vi.mocked(providersService.setKey);
const mockedDeleteKey = vi.mocked(providersService.deleteKey);

const fakeProvider = (overrides: Record<string, unknown> = {}) => ({
  id: 'anthropic',
  display_name: 'Anthropic',
  has_key: false,
  configurable: true,
  is_oauth: false,
  key_source: 'none',
  auth_error: null,
  models: [],
  ...overrides,
});

beforeEach(() => {
  cleanup();
  useProvidersGateStore.setState({ configured: null, checkedAt: null });
  useSettingsTabStore.setState({
    settingsTabActive: true,
    selectedGroup: 'providers',
  });
  mockedGetProviders.mockReset();
  mockedSetKey.mockReset();
  mockedDeleteKey.mockReset();
});

describe('SettingsDetail — providers gate refresh', () => {
  // ProvidersGroup mount is the single source of truth: each time the
  // user opens Settings → Providers, the gate is synced from the backend
  // response. This corrects any stale state left behind by a previous
  // failed `check()` (e.g. transient network blip after a delete would
  // otherwise leave the gate stuck on its previous value).
  it('ProvidersGroup mount syncs the gate from the backend response', async () => {
    useProvidersGateStore.setState({ configured: true }); // simulate stale state
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: false })],
      active_provider: null,
    });
    render(<SettingsDetail />);
    await waitFor(() => {
      expect(useProvidersGateStore.getState().configured).toBe(false);
    });
  });

  // TC-D2 — save key triggers a fresh getProviders + setConfigured(true).
  it('after a successful setKey, the gate flips to configured=true', async () => {
    // Initial ProvidersGroup fetch (no keys).
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: false })],
      active_provider: null,
    });
    mockedSetKey.mockResolvedValue({ ok: true });
    // Post-save refresh — now has key.
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: true })],
      active_provider: 'anthropic',
    });

    const { container, getByPlaceholderText, getByTitle } = render(<SettingsDetail />);
    await waitFor(() => {
      expect(container.querySelector('input[placeholder*="enter_key"]')).not.toBeNull();
    });
    const input = getByPlaceholderText('settings.providers.enter_key');
    fireEvent.change(input, { target: { value: 'sk-test-123' } });
    fireEvent.click(getByTitle('settings.providers.save_key'));

    await waitFor(() => {
      expect(useProvidersGateStore.getState().configured).toBe(true);
    });
    expect(mockedSetKey).toHaveBeenCalledWith('anthropic', 'sk-test-123');
  });

  // TC-D1 — delete key triggers a fresh getProviders + setConfigured(false).
  it('after a successful deleteKey, the gate flips to configured=false', async () => {
    // Initial ProvidersGroup fetch (with key).
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: true })],
      active_provider: 'anthropic',
    });
    mockedDeleteKey.mockResolvedValue({ ok: true });
    // Post-delete refresh — no key.
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: false })],
      active_provider: null,
    });

    const { getByTitle } = render(<SettingsDetail />);
    const deleteBtn = await waitFor(() => getByTitle('settings.providers.clear_key'));
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(useProvidersGateStore.getState().configured).toBe(false);
    });
    expect(mockedDeleteKey).toHaveBeenCalledWith('anthropic');
  });

  it('does not flip the gate when setKey returns ok=false', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: false })],
      active_provider: null,
    });
    mockedSetKey.mockResolvedValue({ ok: false, error: 'bad key' });

    const { container, getByPlaceholderText, getByTitle } = render(<SettingsDetail />);
    await waitFor(() => {
      expect(container.querySelector('input[placeholder*="enter_key"]')).not.toBeNull();
    });
    // ProvidersGroup already synced the gate to false on mount.
    expect(useProvidersGateStore.getState().configured).toBe(false);
    const configuredBefore = useProvidersGateStore.getState().configured;

    fireEvent.change(getByPlaceholderText('settings.providers.enter_key'), {
      target: { value: 'sk-bad' },
    });
    fireEvent.click(getByTitle('settings.providers.save_key'));

    await waitFor(() => {
      expect(container.textContent).toContain('bad key');
    });
    // Failed setKey does NOT touch the gate (no successful post-save
    // fetch is fired, so setConfigured is not called).
    expect(useProvidersGateStore.getState().configured).toBe(configuredBefore);
  });

  // R1 side-effect fix: when post-save fetch fails, fail OPEN.
  // The user just added a key, so the optimistic assumption is that
  // they now have at least one configured provider.
  it('after setKey succeeds but post-save fetch fails, gate fails OPEN to true', async () => {
    useProvidersGateStore.setState({ configured: false });
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: false })],
      active_provider: null,
    });
    mockedSetKey.mockResolvedValue({ ok: true });
    mockedGetProviders.mockRejectedValueOnce(new Error('network'));

    const { container, getByPlaceholderText, getByTitle } = render(<SettingsDetail />);
    await waitFor(() => {
      expect(container.querySelector('input[placeholder*="enter_key"]')).not.toBeNull();
    });
    fireEvent.change(getByPlaceholderText('settings.providers.enter_key'), {
      target: { value: 'sk-test' },
    });
    fireEvent.click(getByTitle('settings.providers.save_key'));

    await waitFor(() => {
      expect(useProvidersGateStore.getState().configured).toBe(true);
    });
  });

  // R1 side-effect fix: when post-delete fetch fails, fail CLOSED.
  // The user just deleted a key, so the pessimistic assumption is that
  // they may now have zero configured providers. This re-engages the
  // gate so chat / tasks clicks show the dialog instead of silently
  // passing through.
  it('after deleteKey succeeds but post-delete fetch fails, gate fails CLOSED to false', async () => {
    useProvidersGateStore.setState({ configured: true });
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: true })],
      active_provider: 'anthropic',
    });
    mockedDeleteKey.mockResolvedValue({ ok: true });
    mockedGetProviders.mockRejectedValueOnce(new Error('network'));

    const { getByTitle } = render(<SettingsDetail />);
    const deleteBtn = await waitFor(() => getByTitle('settings.providers.clear_key'));
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(useProvidersGateStore.getState().configured).toBe(false);
    });
  });
});