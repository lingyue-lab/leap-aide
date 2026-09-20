import { create } from 'zustand';
import { providersService } from '@/services/api';

/**
 * Tracks whether at least one model provider is configured (i.e. has an
 * API key in /api/providers). Used as a precondition gate so that the chat
 * composer and cron jobs (Tasks) panel cannot be entered / sent-to without
 * at least one usable provider.
 *
 * `null` means "not yet checked" — callers should treat that as "unknown,
 * recheck before deciding" rather than "configured" or "unconfigured".
 *
 * `check()` re-fetches /api/providers and updates the flag. On network
 * failure it preserves the previous value (only defaulting to true when
 * there was no prior value), so a transient blip after the user deletes
 * their last key does not silently re-arm the gate. See R1 in the plan.
 *
 * An in-flight Promise is reused for concurrent callers so that App boot's
 * check and a near-simultaneous click on Chat do not issue two network
 * requests.
 */
interface ProvidersGateState {
  configured: boolean | null;
  checkedAt: number | null;
  setConfigured: (configured: boolean) => void;
  check: () => Promise<boolean>;
  reset: () => void;
}

let inFlight: Promise<boolean> | null = null;

export const useProvidersGateStore = create<ProvidersGateState>((set, get) => ({
  configured: null,
  checkedAt: null,
  setConfigured: (configured) => set({ configured, checkedAt: Date.now() }),
  reset: () => set({ configured: null, checkedAt: null }),
  check: () => {
    if (inFlight) return inFlight;
    inFlight = (async () => {
      try {
        const res = await providersService.getProviders();
        const list = res.providers ?? [];
        const configured = list.some((p) => p.has_key);
        set({ configured, checkedAt: Date.now() });
        return configured;
      } catch {
        // On error, keep the previous value if known; only default to
        // fail-open (true) when we have no prior signal at all. This
        // prevents a transient network blip after a delete from silently
        // flipping the gate back to "configured".
        const prev = get().configured;
        const next = prev ?? true;
        set({ configured: next, checkedAt: Date.now() });
        return next;
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  },
}));
