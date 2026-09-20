import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';
import { providersService } from '@/services/api';

vi.mock('@/services/api', () => ({
  providersService: {
    getProviders: vi.fn(),
  },
}));

const mockedGetProviders = vi.mocked(providersService.getProviders);

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

describe('useProvidersGateStore', () => {
  beforeEach(() => {
    useProvidersGateStore.setState({ configured: null, checkedAt: null });
    mockedGetProviders.mockReset();
  });

  // TC-S1
  it('initial state: configured=null, checkedAt=null', () => {
    const { configured, checkedAt } = useProvidersGateStore.getState();
    expect(configured).toBeNull();
    expect(checkedAt).toBeNull();
  });

  // TC-S2
  it('check() with at least one has_key=true sets configured=true', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: fakeProviders([{ has_key: true }]) as never,
      active_provider: 'p0',
    });
    const result = await useProvidersGateStore.getState().check();
    expect(result).toBe(true);
    const { configured, checkedAt } = useProvidersGateStore.getState();
    expect(configured).toBe(true);
    expect(checkedAt).not.toBeNull();
    expect(Date.now() - (checkedAt ?? 0)).toBeLessThan(1000);
  });

  // TC-S3
  it('check() with all has_key=false sets configured=false', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: fakeProviders([{ has_key: false }, { has_key: false }]) as never,
      active_provider: null,
    });
    const result = await useProvidersGateStore.getState().check();
    expect(result).toBe(false);
    expect(useProvidersGateStore.getState().configured).toBe(false);
  });

  // TC-S4 — delete then re-gate: state flips true → false.
  it('flips back to configured=false on subsequent check after delete', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: fakeProviders([{ has_key: true }]) as never,
      active_provider: 'p0',
    });
    await useProvidersGateStore.getState().check();
    expect(useProvidersGateStore.getState().configured).toBe(true);

    mockedGetProviders.mockResolvedValueOnce({
      providers: fakeProviders([{ has_key: false }]) as never,
      active_provider: null,
    });
    const next = await useProvidersGateStore.getState().check();
    expect(next).toBe(false);
    expect(useProvidersGateStore.getState().configured).toBe(false);
  });

  // TC-S5 — first boot, fetch fails: fail-open to true.
  it('fails open to configured=true when prior value is null and check throws', async () => {
    mockedGetProviders.mockRejectedValueOnce(new Error('network'));
    const result = await useProvidersGateStore.getState().check();
    expect(result).toBe(true);
    expect(useProvidersGateStore.getState().configured).toBe(true);
  });

  // TC-S8 — delete then network blip: keep previous false (don't silently re-arm).
  it('preserves prior configured=false on fetch error (does not flip back to true)', async () => {
    // First check succeeds and finds no provider.
    mockedGetProviders.mockResolvedValueOnce({
      providers: fakeProviders([{ has_key: false }]) as never,
      active_provider: null,
    });
    await useProvidersGateStore.getState().check();
    expect(useProvidersGateStore.getState().configured).toBe(false);

    // Subsequent check (e.g. refresh) errors out — keep the false flag.
    mockedGetProviders.mockRejectedValueOnce(new Error('network'));
    const result = await useProvidersGateStore.getState().check();
    expect(result).toBe(false);
    expect(useProvidersGateStore.getState().configured).toBe(false);
  });

  // TC-S6 — manual setConfigured is overwritten by next check result.
  it('check() result overrides a prior setConfigured call', async () => {
    useProvidersGateStore.setState({ configured: true });
    expect(useProvidersGateStore.getState().configured).toBe(true);

    mockedGetProviders.mockResolvedValueOnce({
      providers: fakeProviders([{ has_key: false }]) as never,
      active_provider: null,
    });
    await useProvidersGateStore.getState().check();
    expect(useProvidersGateStore.getState().configured).toBe(false);
  });

  // TC-S7
  it('reset() returns the store to initial state', () => {
    useProvidersGateStore.setState({ configured: true, checkedAt: 123 });
    useProvidersGateStore.getState().reset();
    const { configured, checkedAt } = useProvidersGateStore.getState();
    expect(configured).toBeNull();
    expect(checkedAt).toBeNull();
  });

  // TC-S9 — in-flight guard: two concurrent check() calls share a single request.
  it('shares a single in-flight request across concurrent callers', async () => {
    let resolveOuter!: (v: unknown) => void;
    const outer = new Promise((r) => { resolveOuter = r; });
    mockedGetProviders.mockReturnValueOnce(outer as never);

    const a = useProvidersGateStore.getState().check();
    const b = useProvidersGateStore.getState().check();
    // Only one network call should have been issued.
    expect(mockedGetProviders).toHaveBeenCalledTimes(1);

    resolveOuter({
      providers: fakeProviders([{ has_key: true }]) as never,
      active_provider: 'p0',
    });
    const [ra, rb] = await Promise.all([a, b]);
    expect(ra).toBe(true);
    expect(rb).toBe(true);
    expect(useProvidersGateStore.getState().configured).toBe(true);
  });

  it('handles a missing providers array defensively (empty list → configured=false)', async () => {
    mockedGetProviders.mockResolvedValueOnce({
      providers: undefined as never,
      active_provider: null,
    });
    const result = await useProvidersGateStore.getState().check();
    expect(result).toBe(false);
    expect(useProvidersGateStore.getState().configured).toBe(false);
  });
});
