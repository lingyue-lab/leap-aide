import { useCallback, useEffect, startTransition } from 'react';
import { chatService } from '@/services/api';
import { useChatStore } from '@/stores/useChatStore';
import { useSessionStore } from '@/stores/useSessionStore';
import * as StreamManager from '@/stores/streamManager';
import type { SessionStreamState } from '@/stores/streamManager';
import type { Message } from '@/types';

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

          // ── Queue state consistency check ────────────────────────────────
          // When the agent becomes idle (busy → false), verify that
          // queuedMessages matches the actual ghost messages.  This is a
          // state-based safety net that catches stale queue state regardless
          // of which event path led to the inconsistency.
          const chatState = useChatStore.getState();
          const ghostCount = chatState.messages.filter(m => m._queued).length;
          if (!state.busy) {
            if (chatState.queuedMessages.length !== ghostCount) {
              if (ghostCount === 0) {
                // No ghosts in chat → queue should be empty
                chatState.clearQueuedMessages();
              } else {
                // Ghosts exist but queue is desynced → trim stale ghosts
                const cleaned = chatState.messages.filter(m => !m._queued);
                useChatStore.setState({ messages: cleaned });
                chatState.clearQueuedMessages();
              }
            }
          }

          // When a stream finishes and it's the active session, promote the
          // front queued message (it's now being dequeued by the backend).
          if (!state.streaming && state.completedAt) {
            const currentId = useSessionStore.getState().currentSessionId;
            if (currentId === sessionId) {
              const store = useChatStore.getState();
              if (store.queuedMessages.length > 0) {
                // Identify the front ghost by text content (not _queue_position,
                // which becomes stale after promoteQueuedMessages shifts the array).
                const frontText = store.queuedMessages[0]?.text;
                const filtered = store.messages.filter(
                  (m) => !(m._queued && typeof m.content === 'string' && m.content === frontText)
                );
                useChatStore.setState({ messages: filtered });
                store.promoteQueuedMessages();
              }

              loadSession(sessionId).then(async () => {
                // Re-check: user may have switched sessions while loadSession was in-flight
                if (useSessionStore.getState().currentSessionId !== sessionId) return;
                const session = useSessionStore.getState().currentSession;
                const storeAfterLoad = useChatStore.getState();
                if (session?.messages) {
                  // Preserve ONLY queued ghosts (⏳). Steered messages (↪)
                  // are intentionally dropped — their content is already
                  // persisted in the tool output's OUT-OF-BAND USER MESSAGE block.
                  const remainingGhosts = storeAfterLoad.messages.filter(m => m._queued);
                  const merged = [...session.messages, ...remainingGhosts];
                  useChatStore.getState().setMessages(merged);

                  // Sync queuedMessages state with actual ghost count.
                  // If no ghosts remain, the queue is fully drained.
                  if (remainingGhosts.length === 0) {
                    storeAfterLoad.clearQueuedMessages();
                  }
                }
                if (session?.tool_calls) {
                  useChatStore.getState().setToolCalls(session.tool_calls);
                }
                // Sync the sidebar session list title from the reloaded session
                if (session?.title) {
                  useSessionStore.getState().updateSessionListTitle?.(sessionId, session.title);
                }

                // ── Reconnect to dequeued stream ──────────────────────────
                // When a stream finishes and there are still queued messages,
                // the backend's _dequeue_next has already started a new stream
                // for the next queued message. We must attach an SSE connection
                // to it so the frontend receives its completion event and can
                // continue draining the queue.
                const activeStreamId = session?.active_stream_id;
                const pendingMsg = session?.pending_user_message;
                if (activeStreamId && pendingMsg) {
                  try {
                    const status = await chatService.checkStreamStatus(activeStreamId);
                    if (status.active) {
                      StreamManager.reconnectStream(
                        sessionId,
                        activeStreamId,
                        pendingMsg,
                        session?.messages ?? [],
                      );
                      useChatStore.setState({
                        busy: true,
                        activeStreamId,
                      });
                    }
                  } catch (e) {
                  }
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
    attachments?: string[],
    mode?: string,
  ) => {
    const store = useChatStore.getState();

    // For interrupt mode: stop the front-end SSE connection IMMEDIATELY so
    // the user sees the stream stop, rather than waiting for the backend
    // round-trip during which old tokens would keep arriving.
    if (mode === 'interrupt') {
      StreamManager.stopBackgroundStream(sessionId);
    }

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
      const result = await chatService.startChat(
        sessionId, message, model, workspace, attachments, mode
      );

      // Handle queued/steered responses (no stream_id)
      if (result.queued || result.steered) {
        if (result.queued) {
          // The optimistic user message may have been overwritten by
          // StreamManager token events during the await above. Use a
          // fresh state snapshot and re-add the ghost if necessary.
          const freshState = useChatStore.getState();
          const freshMessages = freshState.messages;
          const lastIdx = freshMessages.length - 1;
          const lastMsg = lastIdx >= 0 ? freshMessages[lastIdx] : null;
          const ghostExists = lastMsg?.role === 'user' && lastMsg.content === message;

          if (ghostExists) {
            // Mark the existing optimistic message as queued
            const updatedMessages = [...freshMessages];
            updatedMessages[lastIdx] = {
              ...updatedMessages[lastIdx],
              _queued: true,
              _queue_position: result.queue_position,
              // Carry steer fallback reason so MessageRow can show it
              _steered: !!result.steer_fallback ? true : undefined,
            };
            useChatStore.setState({ messages: updatedMessages });
          } else {
            // Optimistic message was lost — re-add as ghost
            useChatStore.getState().addMessage({
              role: 'user',
              content: message,
              attachments,
              _ts: Date.now() / 1000,
              _queued: true,
              _queue_position: result.queue_position,
              _steered: !!result.steer_fallback ? true : undefined,
            });
          }
          // Track in queue state
          freshState.addQueuedMessage({
            position: result.queue_position ?? 0,
            text: message,
            attachments,
          });
        }

        if (result.steered) {
          // Steer succeeded — message was injected into the running agent.
          // Mark the optimistic message as steered so the UI can show a subtle
          // indicator (different from queued ghost).
          const freshState = useChatStore.getState();
          const freshMessages = freshState.messages;
          const lastIdx = freshMessages.length - 1;
          if (lastIdx >= 0 && freshMessages[lastIdx].role === 'user') {
            const updatedMessages = [...freshMessages];
            updatedMessages[lastIdx] = {
              ...updatedMessages[lastIdx],
              _steered: true,
            };
            useChatStore.setState({ messages: updatedMessages });
          }
        }

        store.setBusy(false);
        return;
      }

      const stream_id = result.stream_id!;

      // Record active stream id on the store (for cancel button)
      setActiveStreamId(stream_id);

      // Build the seed messages for StreamManager.
      // For interrupt mode: reload from backend to get the authoritative
      // baseline.  useChatStore.messages may contain live SSE tokens from
      // the cancelled stream, which would otherwise leak into the new stream.
      let seedMessages: Message[];
      if (mode === 'interrupt') {
        await loadSession(sessionId);
        const session = useSessionStore.getState().currentSession;
        const msgs: Message[] = session?.messages ?? [];
        // If the agent thread already appended our user message, strip it
        // so startBackgroundStream can prepend it cleanly.
        const last = msgs[msgs.length - 1];
        if (last?.role === 'user' && typeof last.content === 'string' && last.content === message) {
          seedMessages = msgs.slice(0, -1);
        } else {
          seedMessages = msgs;
        }
      } else {
        // Snapshot messages BEFORE the user message we just added above.
        // StreamManager will prepend the user message itself.
        const currentMessages = useChatStore.getState().messages;
        seedMessages = currentMessages.slice(0, currentMessages.length - 1);
      }

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
