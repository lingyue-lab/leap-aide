import { create } from 'zustand';
import { providersService } from '@/services/api';

/**
 * Tracks whether at least one model provider is configured (i.e. has an
 * API key in /api/providers). Used as a precondition gate so that the chat
 * composer and cron jobs (Tasks) panel cannot be entered / sent-to without
 * at least one usable provider.
 *
 * `null` means "not known yet" — callers must recheck before deciding.
 *
 * `check()` caches a *definite* answer only. Two rules keep the gate honest
 * across a clean boot (issue #5):
 *
 *   1. A failed check never invents an answer. If nothing was known yet,
 *      `configured` stays `null` so the next caller asks again. Previously
 *      the boot-time 401 (no session cookie yet) or 503 (server still
 *      initialising) was cached as `configured = true`, which silenced the
 *      gate until Settings → Model Providers overwrote the flag.
 *   2. A failed check preserves a previously known value, so a transient
 *      blip after the user deletes their last key neither re-arms the gate
 *      nor starts nagging a user who does have keys. See R1 in the plan.
 *
 * `lastErrorStatus` carries the HTTP status of the most recent failed check
 * (null for network errors) so `gateProviders()` can distinguish "server
 * still initialising" (503 → worth retrying) from "cannot ask at all"
 * (fail open for this call).
 *
 * An in-flight Promise is reused for concurrent callers so that App boot's
 * check and a near-simultaneous click on Chat do not issue two network
 * requests.
 */
interface ProvidersGateState {
  configured: boolean | null;
  checkedAt: number | null;
  lastErrorStatus: number | null;
  setConfigured: (configured: boolean) => void;
  check: () => Promise<boolean | null>;
  reset: () => void;
}

let inFlight: Promise<boolean | null> | null = null;

export const useProvidersGateStore = create<ProvidersGateState>((set, get) => ({
  configured: null,
  checkedAt: null,
  lastErrorStatus: null,
  setConfigured: (configured) =>
    set({ configured, checkedAt: Date.now(), lastErrorStatus: null }),
  reset: () => set({ configured: null, checkedAt: null, lastErrorStatus: null }),
  check: () => {
    if (inFlight) return inFlight;
    inFlight = (async () => {
      try {
        const res = await providersService.getProviders();
        const list = res.providers ?? [];
        const configured = list.some((p) => p.has_key);
        set({ configured, checkedAt: Date.now(), lastErrorStatus: null });
        return configured;
      } catch (e) {
        const status = (e as { status?: unknown } | null)?.status;
        // Deliberately does NOT write `configured` — rules 1 and 2 above.
        set({
          checkedAt: Date.now(),
          lastErrorStatus: typeof status === 'number' ? status : null,
        });
        return get().configured;
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  },
}));
