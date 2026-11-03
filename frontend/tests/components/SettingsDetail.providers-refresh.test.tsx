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
 *  - A successful save invalidates + reloads the models store so the
 *    compose-footer picker sees new/rejected providers immediately.
 *  - A save that returns a warning shows it while keeping has_key true.
 *  - A stored key with key_status='auth' renders a persistent error line.
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
import { useModelsStore } from '@/stores/useModelsStore';
import { providersService, modelsService } from '@/services/api';

vi.mock('@/services/api', () => ({
  providersService: {
    getProviders: vi.fn(),
    setKey: vi.fn(),
    deleteKey: vi.fn(),
  },
  modelsService: {
    getModels: vi.fn(),
  },
  configFileService: {
    getRaw: vi.fn().mockResolvedValue(''),
    saveRaw: vi.fn(),
  },
  settingsService: {
    setDefaultModel: vi.fn(),
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
const mockedGetModels = vi.mocked(modelsService.getModels);

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
  // Abort any in-flight models load left over from a previous test, then
  // reset to a clean idle store.
  useModelsStore.getState().cancel();
  useModelsStore.setState({ data: null, status: 'idle', error: null, fetchedAt: null });
  mockedGetProviders.mockReset();
  mockedSetKey.mockReset();
  mockedDeleteKey.mockReset();
  mockedGetModels.mockReset();
  mockedGetModels.mockResolvedValue({
    groups: [],
    active_provider: null,
    default_model: '',
    building: false,
  });
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

  // The save flow must refresh the models catalog immediately: ChatComposer
  // only reloads on gate true↔false transitions, so adding a 2nd provider
  // (or fixing a rejected key) would otherwise leave a stale 5-min-TTL picker.
  it('after a successful setKey, the models store is invalidated and reloaded', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: false })],
      active_provider: null,
    });
    mockedSetKey.mockResolvedValue({ ok: true, validated: true, models_count: 2 });
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: true })],
      active_provider: 'anthropic',
    });

    const { container, getByPlaceholderText, getByTitle } = render(<SettingsDetail />);
    await waitFor(() => {
      expect(container.querySelector('input[placeholder*="enter_key"]')).not.toBeNull();
    });
    fireEvent.change(getByPlaceholderText('settings.providers.enter_key'), {
      target: { value: 'sk-test-123' },
    });
    fireEvent.click(getByTitle('settings.providers.save_key'));

    await waitFor(() => {
      expect(useModelsStore.getState().status).toBe('ready');
    });
    expect(mockedGetModels).toHaveBeenCalled();
  });

  // Deleting a key must refresh the catalog immediately too: the provider's
  // models (and the configured default's availability warning) would
  // otherwise stay stale for up to the store's 5-minute TTL.
  it('after a successful deleteKey, the models store is invalidated and reloaded', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: true })],
      active_provider: 'tencent-tokenhub',
    });
    mockedDeleteKey.mockResolvedValue({ ok: true });
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ id: 'tencent-tokenhub', has_key: false })],
      active_provider: 'tencent-tokenhub',
    });

    const { getByTitle } = render(<SettingsDetail />);
    const deleteBtn = await waitFor(() => getByTitle('settings.providers.clear_key'));

    // A fresh catalog that still lists the provider's models.  With the TTL
    // this would otherwise be reused instead of re-fetched.
    useModelsStore.setState({
      data: {
        groups: [
          {
            provider: 'Tencent TokenHub',
            provider_id: 'tencent-tokenhub',
            models: [{ id: '@tencent-tokenhub:hy3-preview', label: 'Hy3 Preview' }],
          },
        ],
      },
      status: 'ready',
      error: null,
      fetchedAt: Date.now(),
    });
    const callsBefore = mockedGetModels.mock.calls.length;

    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(mockedGetModels.mock.calls.length).toBeGreaterThan(callsBefore);
    });
    await waitFor(() => expect(useModelsStore.getState().status).toBe('ready'));
  });

  // Backend saved the key but could not verify it (network/timeout) —
  // a warning is shown and the key is stored (has_key flips to true).
  it('setKey with a warning shows the warning and still marks the key as stored', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: false })],
      active_provider: null,
    });
    mockedSetKey.mockResolvedValue({
      ok: true,
      warning: 'Could not verify the key; it was saved anyway.',
    });
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: true })],
      active_provider: 'anthropic',
    });

    const { container, getByPlaceholderText, getByTitle, queryByTitle } = render(<SettingsDetail />);
    await waitFor(() => {
      expect(container.querySelector('input[placeholder*="enter_key"]')).not.toBeNull();
    });
    fireEvent.change(getByPlaceholderText('settings.providers.enter_key'), {
      target: { value: 'sk-test-123' },
    });
    fireEvent.click(getByTitle('settings.providers.save_key'));

    await waitFor(() => {
      expect(container.textContent).toContain('Could not verify the key');
    });
    // Key was saved: the row switched to the masked display + clear button.
    expect(queryByTitle('settings.providers.clear_key')).not.toBeNull();
  });

  // A stored key the provider rejected (key_status='auth' from the
  // background probe) renders a persistent error line under the row.
  it('renders a persistent invalid-key notice when key_status is auth', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: true, key_status: 'auth' })],
      active_provider: null,
    });

    const { container } = render(<SettingsDetail />);
    await waitFor(() => {
      expect(container.textContent).toContain('settings.providers.key_invalid');
    });
  });

  // Save-time key validation fetches the model list and can take seconds —
  // the row must show an M3 indeterminate spinner while the request is in
  // flight (instead of the save icon).
  it('shows an M3 spinner in the row while the save request is pending', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: false })],
      active_provider: null,
    });
    let resolveSave!: (v: { ok: boolean }) => void;
    mockedSetKey.mockReturnValue(new Promise((r) => { resolveSave = r; }));

    const { container, getByPlaceholderText, getByTitle, queryByTitle } = render(<SettingsDetail />);
    await waitFor(() => {
      expect(container.querySelector('input[placeholder*="enter_key"]')).not.toBeNull();
    });
    fireEvent.change(getByPlaceholderText('settings.providers.enter_key'), {
      target: { value: 'sk-test-123' },
    });
    fireEvent.click(getByTitle('settings.providers.save_key'));

    // Save button swapped for the spinner; input locked.
    await waitFor(() => {
      expect(queryByTitle('settings.providers.save_key')).toBeNull();
      expect(container.querySelector('svg.m3-circular-progress')).not.toBeNull();
      expect(getByPlaceholderText('settings.providers.enter_key')).toBeDisabled();
    });

    resolveSave({ ok: true });
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: true, key_source: 'env_file' })],
      active_provider: 'anthropic',
    });
    await waitFor(() => {
      expect(queryByTitle('settings.providers.save_key')).toBeNull(); // now has-key layout
      expect(container.querySelector('svg.m3-circular-progress')).toBeNull();
    });
  });

  // Right after a save the provider prop is still the pre-save snapshot
  // (key_source 'none'); the badge must jump 未配置 → .env directly, never
  // flashing a meaningless "None" label.
  it('after saving, the source badge shows .env and never the raw "None"', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: false, key_source: 'none' })],
      active_provider: null,
    });
    mockedSetKey.mockResolvedValue({ ok: true, validated: true, models_count: 2 });
    mockedGetProviders.mockResolvedValueOnce({
      providers: [fakeProvider({ has_key: true, key_source: 'env_file' })],
      active_provider: 'anthropic',
    });

    const { container, getByPlaceholderText, getByTitle } = render(<SettingsDetail />);
    await waitFor(() => {
      expect(container.querySelector('input[placeholder*="enter_key"]')).not.toBeNull();
    });
    fireEvent.change(getByPlaceholderText('settings.providers.enter_key'), {
      target: { value: 'sk-test-123' },
    });
    fireEvent.click(getByTitle('settings.providers.save_key'));

    // Wait until the row flipped to has-key; the badge must already be .env.
    await waitFor(() => {
      expect(container.textContent).toContain('.env');
    });
    expect(container.textContent).not.toContain('None');
  });
});