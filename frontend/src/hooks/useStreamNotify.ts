/**
 * useStreamNotify — Global listener for bg-stream-done events.
 *
 * Unlike the subscriber inside useChatStream (which is tied to ChatInterface
 * and gets unmounted when the user switches tabs), this hook registers the
 * notification + loadSessions listener at the App level so it is NEVER removed.
 *
 * This ensures that when a session finishes streaming while the user is on
 * the Tasks/Skills/Memory/etc. tab, the chat tab badge updates correctly.
 */
import { useEffect } from 'react';
import { useSessionStore } from '@/stores/useSessionStore';
import * as StreamManager from '@/stores/streamManager';
import { notifyCompletion } from '@/utils/notify';
import { sessionService } from '@/services/api';
import { showInfoCard } from '@/components/dialogs/InfoCard';

// ── Chat panel visibility tracker ──────────────────────────────────────
// Toggled by NavTabs.switchPanel.  useStreamNotify reads this to determine
// whether the user is actually viewing the chat panel (not just has a
// session selected in the sidebar while on a different panel like Skills).
let _chatPanelActive = true;  // default panel is chat (see Sidebar.tsx activePanel initial state)
export function setChatPanelActive(active: boolean) { _chatPanelActive = active; }
export function isChatPanelActive(): boolean { return _chatPanelActive; }

export function useStreamNotify() {
  useEffect(() => {
    // Application errors (apperror SSE events) surface as InfoCards instead of
    // polluting the conversation history.
    const onAppError = (e: Event) => {
      const { sessionId, message } = (e as CustomEvent).detail as { sessionId: string; message: string };
      showInfoCard(sessionId, message);
    };
    window.addEventListener('app-error-card', onAppError);

    // When any background stream finishes, play sound / send OS notification
    // and refresh the sessions list so message_count is up to date, which
    // enables the unread badge on the chat tab.
    const onDone = async (e: Event) => {
      const { sessionId } = (e as CustomEvent).detail;

      const session = useSessionStore.getState().sessions.find(
        (s) => s.session_id === sessionId
      );
      const sessionTitle = session?.title ?? 'Session';

      notifyCompletion({
        title: 'Hermes — Task Complete',
        body: sessionTitle,
        tag: `hermes-session-${sessionId}`,
      });

      // "Actively viewing" means BOTH:
      // 1. This session is selected in the sidebar (currentSessionId match), AND
      // 2. The chat panel is the visible panel (not Skills/Tasks/Memory/etc.)
      // Without condition 2, switching to Skills while Session A is selected
      // would wrongly mark Session A as seen when its stream finishes.
      const wasActive = _chatPanelActive
        && useSessionStore.getState().currentSessionId === sessionId;

      // Refresh sessions list so message_count is updated.
      // This drives the unread badge: message_count > last_seen_msg_count
      await useSessionStore.getState().loadSessions().catch(() => {/* ignore */});

      // After loadSessions, re-sync the active session's title to the sidebar.
      // loadSessions fetches from state.db which may still have the provisional
      // title (title_from first user message) when the fetch races ahead of the
      // background LLM title generator's DB write.  The SSE title event has
      // already updated currentSession.title by this point — push it back into
      // the sidebar list so both displays agree.
      const active = useSessionStore.getState().currentSession;
      if (active?.session_id && active?.title) {
        useSessionStore.getState().updateSessionListTitle?.(active.session_id, active.title);
      }

      // If the user was actively watching this session when it finished, they
      // have already "read" all messages — mark as seen to avoid a false
      // unread badge when they switch to another session.
      if (wasActive) {
        const ses = useSessionStore.getState().sessions.find(
          (s) => s.session_id === sessionId
        );
        if (ses) {
          const mc = ses.message_count ?? 0;
          useSessionStore.getState().updateSessionLastSeen(sessionId, mc);
          sessionService.markSessionSeen(sessionId, mc).catch(() => {/* ignore */});
        }
      }

      // Clean up the stream state after a short delay
      setTimeout(() => {
        const s = StreamManager.getSessionStreamState(sessionId);
        if (s && !s.streaming) {
          StreamManager.clearSessionStream(sessionId);
        }
      }, 5000);
    };

    window.addEventListener('bg-stream-done', onDone);

    // bg-stream-title: always update the sidebar list item when a title
    // event arrives.  Unlike the listener in useChatStream (which is tied
    // to ChatInterface and can be torn down between done→title), this
    // App-level listener is NEVER removed — guaranteeing the sidebar
    // stays in sync with every title update.
    const onTitle = (e: Event) => {
      const { sessionId, title } = (e as CustomEvent).detail;
      useSessionStore.getState().updateSessionListTitle?.(sessionId, title);
    };
    window.addEventListener('bg-stream-title', onTitle);

    return () => {
      window.removeEventListener('app-error-card', onAppError);
      window.removeEventListener('bg-stream-done', onDone);
      window.removeEventListener('bg-stream-title', onTitle);
    };
  }, []);
}