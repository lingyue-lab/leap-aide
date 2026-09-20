import { useProvidersGateStore } from '@/stores/useProvidersGateStore';

/**
 * Thin React hook wrapper around `useProvidersGateStore`. Returns the
 * current `configured` value plus a stable `recheck` callback that runs
 * `check()` against /api/providers.
 *
 * Use `useProvidersGateStore.getState().check()` directly from non-React
 * code (event handlers, utility modules) instead of going through this
 * hook — the hook is only for components that need to re-render when
 * `configured` changes.
 */
export function useProvidersGate() {
  const configured = useProvidersGateStore((s) => s.configured);
  return {
    configured,
    isUnconfigured: configured === false,
    recheck: () => useProvidersGateStore.getState().check(),
  };
}
