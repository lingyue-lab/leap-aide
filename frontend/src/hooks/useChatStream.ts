import { useCallback, useEffect, startTransition } from 'react';
import { chatService } from '@/services/api';
import { useChatStore } from '@/stores/useChatStore';
import { useSessionStore } from '@/stores/useSessionStore';
import { showBackgroundTaskCard } from '@/components/dialogs/BackgroundTaskCard';
import { showInfoCard, dismissInfoCard } from '@/components/dialogs/InfoCard';
import { refreshGoalStatus } from '@/components/chat/GoalStatusBar';
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
            phase: state.phase,
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

    // Optimistically add the user message to the live display.
    // For slash commands, defer — the response determines whether the
    // message should appear (card commands show no user message at all).
    const isSlash = message.startsWith('/');
    if (!isSlash) {
      store.addMessage({
        role: 'user',
        content: message,
        attachments,
        _ts: Date.now() / 1000,
      });
    }
    store.setBusy(true);

    // ── Slash commands: show loading card immediately, support cancel ──
    let abortController: AbortController | undefined;
    if (isSlash) {
      abortController = new AbortController();
      showInfoCard("Running command...", {
        sticky: true,
        loading: true,
        onDismiss: () => abortController?.abort(),
      });
    }

    try {
      // Start the chat on the backend
      const result = await chatService.startChat(
        sessionId, message, model, workspace, attachments, mode,
        abortController?.signal,
      );

      // Handle direct command response (slash commands like /help, /model, /clear)
      if (result.direct) {
        // ── Background task: show card notification instead of chat message ──
        const update = result.session_update as Record<string, unknown> | undefined;
        if (update?.background_session) {
          store.setBusy(false);
          const bg = update.background_session as {
            session_id: string; title: string; created_at: number; workspace?: string;
            stream_id: string; prompt: string;
          };
          // Show floating notification card
          showBackgroundTaskCard(bg);
          // Connect to the background session's SSE stream so the sidebar shows
          // the streaming indicator and tokens accumulate for live viewing.
          StreamManager.startBackgroundStream(
            bg.session_id,
            bg.stream_id,
            { role: 'user' as const, content: bg.prompt, _ts: Date.now() / 1000 },
            [],
          );
          // Optimistically prepend to sidebar session list
          const sStore = useSessionStore.getState();
          const entry = {
            session_id: bg.session_id, title: bg.title,
            created_at: bg.created_at, pinned: false, workspace: bg.workspace,
          };
          const current = sStore.sessions || [];
          if (!current.some((s) => s.session_id === bg.session_id)) {
            useSessionStore.setState({ sessions: [entry, ...current] });
          }
          sStore.loadSessions().catch(() => {});
          return;
        }

        // ── Info-card commands: show card instead of chat message ──
        if (update?._as_card) {
          store.setBusy(false);
          // Apply session updates before showing card (e.g. /model changes)
          if (update.model) {
            const { updateSessionModel: _usm } = useSessionStore.getState();
            _usm(sessionId, update.model as string).catch(() => {});
          }
          if (update.title) {
            const { updateCurrentSession, loadSessions: _ls2 } = useSessionStore.getState();
            updateCurrentSession({ title: update.title as string });
            _ls2().catch(() => {});
          }
          if (typeof update.show_reasoning === 'boolean') {
            store.setShowReasoning(update.show_reasoning as boolean);
          }
          if ('reasoning_effort' in (update as Record<string, unknown>)) {
            const { updateSessionReasoningEffort: _usre } = useSessionStore.getState();
            _usre(sessionId, (update as Record<string, unknown>).reasoning_effort as string | null).catch(() => {});
          }
          if ('personality' in (update as Record<string, unknown>)) {
            const { updateSessionPersonality: _usp } = useSessionStore.getState();
            _usp(sessionId, (update as Record<string, unknown>).personality as string | null).catch(() => {});
          }
          if ('service_tier' in (update as Record<string, unknown>)) {
            const { updateCurrentSession: _ucs } = useSessionStore.getState();
            _ucs({ service_tier: (update as Record<string, unknown>).service_tier as string | null });
          }
          showInfoCard(result.content || '', { sticky: true });
          refreshGoalStatus();
          return;
        }

        // ── Normal direct commands: add message to chat history ──
        if (isSlash) {
          store.addMessage({
            role: 'user',
            content: message,
            attachments,
            _ts: Date.now() / 1000,
          });
        }
        store.addMessage({
          role: 'assistant',
          content: result.content || '',
          _ts: Date.now() / 1000,
        });
        store.setBusy(false);

        // Apply session state updates from command
        if (update) {
          if (update.model) {
            const { updateSessionModel } = useSessionStore.getState();
            updateSessionModel(sessionId, update.model as string).catch(() => {});
          }
          if (update.title) {
            const { updateCurrentSession, loadSessions } = useSessionStore.getState();
            updateCurrentSession({ title: update.title as string });
            loadSessions().catch(() => {});
          }
          if (update.cleared) {
            // Reload session to get cleared messages
            const { loadSession } = useSessionStore.getState();
            loadSession(sessionId).catch(() => {});
          }
          if ('personality' in (update as Record<string, unknown>)) {
            const { updateSessionPersonality: _usp } = useSessionStore.getState();
            _usp(sessionId, (update as Record<string, unknown>).personality as string | null).catch(() => {});
          }
          if ('service_tier' in (update as Record<string, unknown>)) {
            const { updateCurrentSession: _ucs } = useSessionStore.getState();
            _ucs({ service_tier: (update as Record<string, unknown>).service_tier as string | null });
          }
          if (update.new_session_id) {
            // Navigate to the new session
            const newSid = update.new_session_id as string;
            window.history.replaceState(null, '', `?session=${encodeURIComponent(newSid)}`);
            const { loadSession, selectSession, loadSessions } = useSessionStore.getState();
            selectSession(newSid);
            loadSession(newSid).catch(() => {});
            loadSessions().catch(() => {});
          }
        }
        return;
      }

      // Handle queued/steered responses (no stream_id)
      if (result.queued || result.steered) {
        // Strip slash-command prefix so the message shows the actual prompt,
        // not "/queue review the code". The backend already stores the clean
        // prompt in the queue / steers it directly.
        let cleanMsg = message;
        if (cleanMsg.startsWith('/queue ')) cleanMsg = cleanMsg.slice(7);
        else if (cleanMsg.startsWith('/steer ')) cleanMsg = cleanMsg.slice(7);
        else if (cleanMsg.startsWith('/q ')) cleanMsg = cleanMsg.slice(3);

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
              content: cleanMsg,
              _queued: true,
              _queue_position: result.queue_position,
              // Carry steer fallback reason so MessageRow can show it
              _steered: !!result.steer_fallback ? true : undefined,
            };
            useChatStore.setState({ messages: updatedMessages });
          } else {
            // Optimistic message was lost or never added (slash cmd) — re-add as ghost
            useChatStore.getState().addMessage({
              role: 'user',
              content: cleanMsg,
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
            text: cleanMsg,
            attachments,
          });
        }

        if (result.steered) {
          // Steer succeeded — message was injected into the running agent.
          const freshState = useChatStore.getState();
          const freshMessages = freshState.messages;
          const lastIdx = freshMessages.length - 1;
          const lastMsg = lastIdx >= 0 ? freshMessages[lastIdx] : null;
          const ghostExists = lastMsg?.role === 'user' && lastMsg.content === message;

          if (ghostExists) {
            // Mark the existing optimistic message as steered
            const updatedMessages = [...freshMessages];
            updatedMessages[lastIdx] = {
              ...updatedMessages[lastIdx],
              content: cleanMsg,
              _steered: true,
            };
            useChatStore.setState({ messages: updatedMessages });
          } else if (isSlash) {
            // No optimistic message — add ghost steered indicator
            useChatStore.getState().addMessage({
              role: 'user',
              content: cleanMsg,
              attachments,
              _ts: Date.now() / 1000,
              _steered: true,
            });
          }
        }

        store.setBusy(false);
        return;
      }

      // For slash commands that reach the streaming path (skills, /retry,
      // /undo), dismiss the loading card and add the user message now.
      // Regular messages were already optimistically added above; the
      // seed-message logic below will strip it and StreamManager prepends
      // it cleanly.  The user message is shown in chat history with its
      // original_content (the raw /command text) when available.
      if (isSlash) {
        dismissInfoCard();
        store.addMessage({
          role: 'user',
          content: message,
          attachments,
          _ts: Date.now() / 1000,
        });
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
      // AbortError from user dismissing the loading card — just dismiss silently
      if ((err as Error).name === 'AbortError') {
        showInfoCard("Command cancelled.", { sticky: false });
        return;
      }
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
