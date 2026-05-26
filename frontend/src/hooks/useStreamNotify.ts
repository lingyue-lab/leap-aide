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

export function useStreamNotify() {
  useEffect(() => {
    // When any background stream finishes, play sound / send OS notification
    // and refresh the sessions list so message_count is up to date, which
    // enables the unread badge on the chat tab.
    const onDone = (e: Event) => {
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

      // Refresh sessions list so message_count is updated.
      // This drives the unread badge: message_count > last_seen_msg_count
      useSessionStore.getState().loadSessions().catch(() => {/* ignore */});

      // Clean up the stream state after a short delay
      setTimeout(() => {
        const s = StreamManager.getSessionStreamState(sessionId);
        if (s && !s.streaming) {
          StreamManager.clearSessionStream(sessionId);
        }
      }, 5000);
    };

    window.addEventListener('bg-stream-done', onDone);

    return () => {
      window.removeEventListener('bg-stream-done', onDone);
    };
  }, []);
}