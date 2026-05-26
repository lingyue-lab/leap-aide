import { useCallback, useEffect, startTransition } from 'react';
import { chatService } from '@/services/api';
import { useChatStore } from '@/stores/useChatStore';
import { useSessionStore } from '@/stores/useSessionStore';
import * as StreamManager from '@/stores/streamManager';
import type { SessionStreamState } from '@/stores/streamManager';

/**
 * useChatStream
 *
 * Thin React adapter over the global StreamManager.
 *
 * - startStream: starts a background SSE stream via StreamManager.
 *   The stream continues even if the user switches sessions.
 * - cancelStream: cancels a stream (closes EventSource + calls backend).
 * - The hook subscribes to StreamManager changes and syncs them into
 *   useChatStore via updateSessionCache, so:
 *     • background sessions accumulate tokens in their cache slice
 *     • the active (displayed) session is updated live in the store
 */
export function useChatStream() {
  const { updateSessionCache, setActiveStreamId } = useChatStore();
  const { updateCurrentSession, loadSession } = useSessionStore();

  // ── Subscribe to StreamManager events ──────────────────────────────────────
  useEffect(() => {
    const unsubscribe = StreamManager.subscribe(
      (sessionId: string, state: SessionStreamState) => {
        // Important state changes (stream start/end/error) update synchronously
        const isImportantUpdate = !state.streaming || state.completedAt != null;

        const doUpdate = () => {
          updateSessionCache(sessionId, {
            messages: state.messages,
            toolCalls: state.toolCalls,
            busy: state.busy,
            activeStreamId: state.streaming ? state.streamId : null,
            lastUsage: state.lastUsage ?? null,
          });

          // When a stream finishes and it's the active session, reload from
          // backend to get the fully-persisted version (tool outputs etc.)
          if (!state.streaming && state.completedAt) {
            const currentId = useSessionStore.getState().currentSessionId;
            if (currentId === sessionId) {
              loadSession(sessionId).then(() => {
                // Re-check: user may have switched sessions while loadSession was in-flight
                if (useSessionStore.getState().currentSessionId !== sessionId) return;
                const session = useSessionStore.getState().currentSession;
                if (session?.messages) {
                  useChatStore.getState().setMessages(session.messages);
                }
                if (session?.tool_calls) {
                  useChatStore.getState().setToolCalls(session.tool_calls);
                }
                // Sync the sidebar session list title from the reloaded session
                if (session?.title) {
                  useSessionStore.getState().updateSessionListTitle?.(sessionId, session.title);
                }
              });
            }
          }
        };

        if (isImportantUpdate) {
          // Sync update for important events (stream done/error/start)
          doUpdate();
        } else {
          // Use startTransition for token updates — allows React to interrupt
          // this render if higher-priority work comes in (user interaction etc.)
          // This is the key fix: prevents token updates from blocking the main thread
          startTransition(doUpdate);
        }
      }
    );

    // Handle title updates from StreamManager (custom window event)
    const onTitle = (e: Event) => {
      const { sessionId, title } = (e as CustomEvent).detail;
      const currentId = useSessionStore.getState().currentSessionId;
      if (currentId === sessionId) {
        updateCurrentSession({ title });
      }
      // Update the session list entry regardless
      useSessionStore.getState().updateSessionListTitle?.(sessionId, title);
    };
    window.addEventListener('bg-stream-title', onTitle);

    // NOTE: bg-stream-done listener is now in useStreamNotify (registered at
    // App level) so it persists across tab switches. We only keep the title
    // listener here because it needs the updateCurrentSession callback.

    return () => {
      unsubscribe();
      window.removeEventListener('bg-stream-title', onTitle);
    };
  }, [updateSessionCache, updateCurrentSession, loadSession]);

  // ── startStream ────────────────────────────────────────────────────────────
  const startStream = useCallback(async (
    sessionId: string,
    message: string,
    model: string,
    workspace?: string,
    attachments?: string[]
  ) => {
    const store = useChatStore.getState();

    // Optimistically add the user message to the live display
    store.addMessage({
      role: 'user',
      content: message,
      attachments,
      _ts: Date.now() / 1000,
    });
    store.setBusy(true);

    try {
      // Start the chat on the backend
      const { stream_id } = await chatService.startChat(
        sessionId, message, model, workspace, attachments
      );

      // Record active stream id on the store (for cancel button)
      setActiveStreamId(stream_id);

      // Snapshot messages BEFORE the user message we just added above.
      // StreamManager will prepend the user message itself.
      const currentMessages = useChatStore.getState().messages;
      const seedMessages = currentMessages.slice(0, currentMessages.length - 1);

      // Hand off to the global StreamManager.
      // It opens the EventSource, accumulates all tokens into the session cache,
      // and keeps running even after the user navigates to another session.
      StreamManager.startBackgroundStream(
        sessionId,
        stream_id,
        { role: 'user', content: message, attachments, _ts: Date.now() / 1000 },
        seedMessages
      );

    } catch (err) {
      store.setBusy(false);
      store.setAbortController(null);
      const errorMsg = (err as Error).message || 'Unknown error';
      store.addMessage({
        role: 'assistant',
        content: `**Error:** ${errorMsg}`,
        _ts: Date.now() / 1000,
      });
    }
  }, [setActiveStreamId]);

  // ── cancelStream ──────────────────────────────────────────────────────────
  const cancelStream = useCallback(async (streamId: string) => {
    // Find which session owns this stream
    const sessionId = StreamManager.getActiveSessionIds().find((sid) => {
      const s = StreamManager.getSessionStreamState(sid);
      return s?.streamId === streamId;
    });

    if (sessionId) {
      StreamManager.stopBackgroundStream(sessionId);
    }

    try {
      await chatService.cancelStream(streamId);
    } catch {
      // Ignore cancel errors
    }

    useChatStore.getState().setBusy(false);
    useChatStore.getState().setAbortController(null);
  }, []);

  const busy = useChatStore((s) => s.busy);

  return { startStream, cancelStream, streaming: busy };
}
