import { create } from 'zustand';
import { modelsService } from '@/services/api';
import type { ModelsResponse } from '@/types';

/**
 * Shared model-catalog store (issue #16).
 *
 * Previously 4 components (ChatComposer mount, the model-picker dialog,
 * MainContent welcome page, TaskForm) each fired their own GET /api/models,
 * so a cold backend build saw 6+ concurrent requests pile up.  All of them
 * now go through this store: concurrent callers share one in-flight request,
 * and a completed catalog is reused for MODELS_TTL_MS.
 *
 * The backend flags a degraded answer with `building: true` (its cold build
 * is still running on a background worker).  In that case status stays
 * 'loading' and we poll every POLL_INTERVAL_MS up to MAX_POLLS times until a
 * real catalog lands — the picker keeps its spinner instead of showing a
 * permanently-empty list.
 *
 * `cancel()` aborts the in-flight request (wired to the picker dialog's X)
 * and stops polling, but keeps whatever data we already have — closing the
 * dialog never destroys progress; reopening resumes from the store.
 */
const MODELS_TTL_MS = 5 * 60 * 1000;
const POLL_INTERVAL_MS = 2500;
const MAX_POLLS = 20;

export type ModelsStatus = 'idle' | 'loading' | 'ready' | 'error';

interface ModelsState {
  data: ModelsResponse | null;
  status: ModelsStatus;
  error: string | null;
  fetchedAt: number | null;
  load: (opts?: { force?: boolean }) => Promise<void>;
  cancel: () => void;
  invalidate: () => void;
}

interface ActiveRequest {
  ctrl: AbortController;
  previousStatus: ModelsStatus;
}

// Module-level coordination (the store itself stays serializable state):
// `active` dedupes concurrent callers and lets cancel()/invalidate() abort
// the live fetch.  A cancelled request is detached from `active` BEFORE its
// abort fires, so neither its teardown nor its AbortError handler may touch
// store state that a newer request already owns.
let active: ActiveRequest | null = null;
let inFlight: Promise<void> | null = null;
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let pollCount = 0;

function stopPollTimer() {
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function clearPoll() {
  stopPollTimer();
  pollCount = 0;
}

export const useModelsStore = create<ModelsState>((set, get) => {
  function schedulePoll() {
    stopPollTimer();
    pollCount += 1;
    if (pollCount > MAX_POLLS) return;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      void get().load({ force: true });
    }, POLL_INTERVAL_MS);
  }

  return {
    data: null,
    status: 'idle',
    error: null,
    fetchedAt: null,

    load: (opts) => {
      const { force = false } = opts ?? {};
      const { status: curStatus, data: curData, fetchedAt } = get();

      // Fresh and complete: skip the network entirely.
      if (!force && curStatus === 'ready' && curData && !curData.building) {
        const age = Date.now() - (fetchedAt ?? 0);
        if (age < MODELS_TTL_MS) return Promise.resolve();
      }

      // Join the in-flight request instead of stacking a second one.
      if (inFlight) return inFlight;

      const req: ActiveRequest = {
        ctrl: new AbortController(),
        previousStatus: get().status,
      };
      active = req;
      set({ status: 'loading', error: null });

      const p = (async () => {
        try {
          const resp = await modelsService.getModels({ signal: req.ctrl.signal });
          if (active !== req) return; // superseded by cancel()/invalidate()
          if (resp.building) {
            // Degraded answer: keep the spinner, keep any previous list on
            // screen, and poll until the backend's background build lands.
            set({
              data: resp,
              fetchedAt: Date.now(),
              // Stay 'loading' — the catalog is not final yet.
            });
            schedulePoll();
          } else {
            clearPoll();
            set({ data: resp, status: 'ready', error: null, fetchedAt: Date.now() });
          }
        } catch (err) {
          if (active !== req) return; // superseded — store state belongs to a newer request
          const aborted =
            (err instanceof DOMException && err.name === 'AbortError') ||
            (err instanceof Error && err.name === 'AbortError');
          if (aborted) {
            // Cancelled (dialog X): not an error — restore the prior status
            // and keep whatever data we already had, so a later open can
            // resume cleanly.
            set({ status: req.previousStatus });
          } else {
            set({
              status: 'error',
              error: err instanceof Error ? err.message : String(err),
            });
          }
        } finally {
          if (active === req) {
            active = null;
            inFlight = null;
          }
        }
      })();
      inFlight = p;
      return p;
    },

    cancel: () => {
      clearPoll();
      const req = active;
      // Detach BEFORE aborting: the aborted request's catch/finally must not
      // touch state owned by a load() that starts right after this cancel.
      // The status restore happens HERE (not in the catch) for the same
      // ownership reason.
      active = null;
      inFlight = null;
      if (req) {
        set({ status: req.previousStatus });
        req.ctrl.abort();
      }
    },

    invalidate: () => {
      clearPoll();
      const req = active;
      active = null;
      inFlight = null;
      if (req) req.ctrl.abort();
      set({ data: null, status: 'idle', error: null, fetchedAt: null });
    },
  };
});
