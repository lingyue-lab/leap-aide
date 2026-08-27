/**
 * sessionSeen — Unified "mark session as seen" with msg_count dedup.
 *
 * All trigger points (click, scroll, focus, auto-read) call `markSessionSeen()`.
 * The function tracks `lastSentMsgCount` per session and skips the API call
 * when the count hasn't changed, avoiding redundant network requests.
 */
import { useSessionStore } from '@/stores/useSessionStore';
import { sessionService } from '@/services/api';
import { ts } from '@/utils/logTs';

/** Per-session msg_count already sent to the backend. */
const lastSentMsgCount = new Map<string, number>();

/**
 * Mark a session as seen (clear unread badge).
 *
 * Dedup: skips the API call when `msg_count` equals the last sent value.
 * Optimistic: updates the store immediately before the network round-trip.
 */
export function markSessionSeen(sessionId: string): void {
  const sessions = useSessionStore.getState().sessions;
  const session = sessions.find((s) => s.session_id === sessionId);
  if (!session) return;

  const msgCount = session.message_count ?? 0;
  const lastSeen = session.last_seen_msg_count ?? 0;

  // No unread messages — nothing to do
  if (msgCount <= lastSeen) return;

  // Already sent this exact count — skip
  if (lastSentMsgCount.get(sessionId) === msgCount) {
    console.debug(`[${ts()}] [SessionSeen] skipped (dedup)`, { sessionId: sessionId.slice(0, 12), msgCount });
    return;
  }

  console.debug(`[${ts()}] [SessionSeen] marking seen`, {
    sessionId: sessionId.slice(0, 12),
    msgCount,
    lastSeen,
    unread: msgCount - lastSeen,
  });

  // Optimistic update
  useSessionStore.getState().updateSessionLastSeen(sessionId, msgCount);
  lastSentMsgCount.set(sessionId, msgCount);

  // Persist to backend (fire-and-forget)
  sessionService.markSessionSeen(sessionId, msgCount).catch(() => {
    // On failure, clear the cache so the next call retries
    lastSentMsgCount.delete(sessionId);
  });
}
