/**
 * Unified session busy-state query.
 *
 * All UI elements (pulse dot, paper plane, send/cancel button, textarea disabled)
 * MUST use these functions instead of reading useChatStore.busy or
 * StreamManager.isSessionStreaming directly.
 *
 * StreamManager is the single source of truth for busy state.
 */

import { useState, useEffect, useRef } from 'react';
import * as StreamManager from '@/stores/streamManager';
import { useChatStore } from '@/stores/useChatStore';

/**
 * Non-reactive getter — use in callbacks / event handlers where you need a
 * point-in-time snapshot.
 *
 * @param sessionId  If provided, check this specific session.
 *                   If omitted, check the currently-displayed session.
 */
export function getIsSessionBusy(sessionId?: string): boolean {
  const sid = sessionId ?? useChatStore.getState()._currentSessionId;
  if (!sid) return false;
  return StreamManager.isSessionStreaming(sid);
}

/**
 * React hook — subscribes to StreamManager and re-renders when busy state
 * changes for the target session.
 *
 * @param sessionId  If provided, track this specific session (for sidebar items).
 *                   If omitted, track the currently-displayed session and
 *                   follow _currentSessionId changes.
 */
export function useIsBusy(sessionId?: string): boolean {
  const [busy, setBusy] = useState<boolean>(() => getIsSessionBusy(sessionId));
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  useEffect(() => {
    // When no explicit sessionId, also track _currentSessionId changes
    let currentSid = sessionId ?? useChatStore.getState()._currentSessionId;

    const unsub = StreamManager.subscribe((changedSid: string) => {
      const targetSid = sessionIdRef.current ?? currentSid;
      if (changedSid === targetSid) {
        const next = StreamManager.isSessionStreaming(targetSid);
        setBusy(next);
      }
    });

    // If tracking the current session, also react to session switches
    let unsubStore: (() => void) | undefined;
    if (!sessionId) {
      unsubStore = useChatStore.subscribe((state, prevState) => {
        if (state._currentSessionId !== prevState._currentSessionId) {
          currentSid = state._currentSessionId;
          const next = currentSid ? StreamManager.isSessionStreaming(currentSid) : false;
          setBusy(next);
        }
      });
    }

    return () => {
      unsub();
      unsubStore?.();
    };
  }, [sessionId]);

  return busy;
}
