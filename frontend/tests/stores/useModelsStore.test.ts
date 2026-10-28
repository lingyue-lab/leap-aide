import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useModelsStore } from '@/stores/useModelsStore';
import { modelsService } from '@/services/api';
import type { ModelsResponse } from '@/types';

vi.mock('@/services/api', () => ({
  modelsService: {
    getModels: vi.fn(),
  },
}));

const mockedGetModels = vi.mocked(modelsService.getModels);

const fullCatalog: ModelsResponse = {
  groups: [
    {
      provider: 'DeepSeek',
      models: [{ id: '@deepseek:chat', label: 'Chat' }],
    },
  ],
  default_model: '@deepseek:chat',
  active_provider: 'deepseek',
};

const buildingCatalog: ModelsResponse = {
  groups: [],
  building: true,
  config_only: true,
};

/** Simulate an abortable fetch: resolves after `ms`, or rejects with AbortError
 *  when the passed signal fires first. */
function deferredResponse<T>(value: T, signal?: AbortSignal) {
  return new Promise<T>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = setTimeout(() => resolve(value), 10);
    signal?.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    });
  });
}

describe('useModelsStore', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // invalidate() resets store state AND the module-level coordination
    // (in-flight controller, poll timer), so every test starts clean.
    useModelsStore.getState().invalidate();
    mockedGetModels.mockReset();
  });

  afterEach(() => {
    useModelsStore.getState().invalidate();
    vi.useRealTimers();
  });

  it('starts idle with no data', () => {
    const { data, status, error, fetchedAt } = useModelsStore.getState();
    expect(data).toBeNull();
    expect(status).toBe('idle');
    expect(error).toBeNull();
    expect(fetchedAt).toBeNull();
  });

  it('concurrent loads share a single in-flight request', async () => {
    let resolveOuter!: (v: ModelsResponse) => void;
    mockedGetModels.mockReturnValueOnce(
      new Promise((r) => {
        resolveOuter = r;
      }) as never
    );

    const a = useModelsStore.getState().load();
    const b = useModelsStore.getState().load();
    expect(mockedGetModels).toHaveBeenCalledTimes(1);

    resolveOuter(fullCatalog);
    await Promise.all([a, b]);

    const { data, status } = useModelsStore.getState();
    expect(status).toBe('ready');
    expect(data?.groups).toEqual(fullCatalog.groups);
  });

  it('TTL-fresh ready catalog skips the network; force refetches', async () => {
    mockedGetModels.mockResolvedValueOnce(fullCatalog as never);
    await useModelsStore.getState().load();
    expect(mockedGetModels).toHaveBeenCalledTimes(1);

    // Within the 5-minute TTL: no new request.
    await useModelsStore.getState().load();
    expect(mockedGetModels).toHaveBeenCalledTimes(1);

    // force: true bypasses the TTL.
    mockedGetModels.mockResolvedValueOnce(fullCatalog as never);
    await useModelsStore.getState().load({ force: true });
    expect(mockedGetModels).toHaveBeenCalledTimes(2);
  });

  it('cancel() aborts the in-flight request, keeps old data, and allows a reload', async () => {
    // First load succeeds so we have data to preserve.
    mockedGetModels.mockResolvedValueOnce(fullCatalog as never);
    await useModelsStore.getState().load();
    const firstData = useModelsStore.getState().data;
    expect(firstData).not.toBeNull();

    // Second (forced) load hangs until aborted.
    mockedGetModels.mockImplementation(
      ((init?: RequestInit) =>
        deferredResponse(fullCatalog, init?.signal)) as never
    );
    const p = useModelsStore.getState().load({ force: true });
    expect(useModelsStore.getState().status).toBe('loading');

    useModelsStore.getState().cancel();
    await p; // AbortError is swallowed — cancel is not an error.

    // Old data is preserved; status restored (not 'error').
    const after = useModelsStore.getState();
    expect(after.data).toBe(firstData);
    expect(after.status).toBe('ready');
    expect(after.error).toBeNull();

    // A reload after cancel issues a fresh request.
    mockedGetModels.mockResolvedValueOnce(fullCatalog as never);
    await useModelsStore.getState().load({ force: true });
    expect(mockedGetModels).toHaveBeenCalledTimes(3);
    expect(useModelsStore.getState().status).toBe('ready');
  });

  it('building:true keeps status loading and polls again after 2.5s', async () => {
    mockedGetModels.mockResolvedValueOnce(buildingCatalog as never);
    void useModelsStore.getState().load();

    // Let the first (degraded) response land.
    await vi.advanceTimersByTimeAsync(0);
    expect(mockedGetModels).toHaveBeenCalledTimes(1);
    expect(useModelsStore.getState().status).toBe('loading');
    expect(useModelsStore.getState().data?.building).toBe(true);

    // A poll fires at +2.5s and this time the real catalog lands.
    mockedGetModels.mockResolvedValueOnce(fullCatalog as never);
    await vi.advanceTimersByTimeAsync(2500);
    expect(mockedGetModels).toHaveBeenCalledTimes(2);
    expect(useModelsStore.getState().status).toBe('ready');
    expect(useModelsStore.getState().data?.building).toBeUndefined();

    // No further polls once ready.
    await vi.advanceTimersByTimeAsync(10_000);
    expect(mockedGetModels).toHaveBeenCalledTimes(2);
  });

  it('polling is capped at MAX_POLLS and settles instead of spinning forever', async () => {
    mockedGetModels.mockResolvedValue(buildingCatalog as never);
    void useModelsStore.getState().load();
    await vi.advanceTimersByTimeAsync(0);
    expect(mockedGetModels).toHaveBeenCalledTimes(1);
    expect(useModelsStore.getState().status).toBe('loading');

    // Well past the poll budget: initial request + at most 36 polls.
    await vi.advanceTimersByTimeAsync(40 * 2500);
    expect(mockedGetModels.mock.calls.length).toBeLessThanOrEqual(37);
    expect(mockedGetModels.mock.calls.length).toBeGreaterThanOrEqual(36);

    // The cap must settle the store — status is no longer 'loading', so the
    // picker cannot freeze on an endless spinner (regression: compose-footer
    // model picker stuck spinning when the backend build hung).
    const after = useModelsStore.getState();
    expect(after.status).not.toBe('loading');
    // Degraded catalog data exists: surface it as ready so the picker shows
    // whatever baseline groups arrived.
    expect(after.status).toBe('ready');
    expect(after.data?.building).toBe(true);
  });

  it('a failed poll lands in error instead of staying loading', async () => {
    mockedGetModels
      .mockResolvedValueOnce(buildingCatalog as never)
      .mockRejectedValue(new Error('network down') as never);
    void useModelsStore.getState().load();
    await vi.advanceTimersByTimeAsync(0);
    expect(useModelsStore.getState().status).toBe('loading');

    await vi.advanceTimersByTimeAsync(2500);
    const after = useModelsStore.getState();
    expect(after.status).toBe('error');
    expect(after.error).toBe('network down');
  });

  it('cancel during a building poll chain restores a settled status', async () => {
    mockedGetModels.mockResolvedValue(buildingCatalog as never);
    void useModelsStore.getState().load();
    await vi.advanceTimersByTimeAsync(0);
    expect(useModelsStore.getState().status).toBe('loading');

    // Let one poll fire so the chain is mid-flight with previousStatus='loading'.
    await vi.advanceTimersByTimeAsync(2500);
    expect(useModelsStore.getState().status).toBe('loading');

    useModelsStore.getState().cancel();
    // Cancel must not restore 'loading' — that re-froze the picker.
    const after = useModelsStore.getState();
    expect(after.status).not.toBe('loading');
    // Degraded data is present, so 'ready' is the settled fallback.
    expect(after.status).toBe('ready');
  });

  it('invalidate() resets everything', async () => {
    mockedGetModels.mockResolvedValueOnce(fullCatalog as never);
    await useModelsStore.getState().load();
    expect(useModelsStore.getState().status).toBe('ready');

    useModelsStore.getState().invalidate();
    const { data, status, error, fetchedAt } = useModelsStore.getState();
    expect(data).toBeNull();
    expect(status).toBe('idle');
    expect(error).toBeNull();
    expect(fetchedAt).toBeNull();
  });

  it('network error sets status=error with the message', async () => {
    mockedGetModels.mockRejectedValueOnce(new Error('HTTP 500') as never);
    await useModelsStore.getState().load();
    const { status, error } = useModelsStore.getState();
    expect(status).toBe('error');
    expect(error).toBe('HTTP 500');
  });
});
