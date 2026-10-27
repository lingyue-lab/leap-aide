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

  it('polling is capped at MAX_POLLS while the backend stays in building state', async () => {
    mockedGetModels.mockResolvedValue(buildingCatalog as never);
    void useModelsStore.getState().load();
    await vi.advanceTimersByTimeAsync(0);
    expect(mockedGetModels).toHaveBeenCalledTimes(1);

    // 25 polls' worth of time: the initial request + at most 20 polls.
    await vi.advanceTimersByTimeAsync(21 * 2500);
    expect(mockedGetModels.mock.calls.length).toBeLessThanOrEqual(21);
    // And the cap actually stopped it well before the window ended.
    expect(mockedGetModels.mock.calls.length).toBeGreaterThanOrEqual(20);
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
