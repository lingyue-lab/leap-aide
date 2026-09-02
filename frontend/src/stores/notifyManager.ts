/**
 * notifyManager — Global singleton for session notifications via WebSocket.
 *
 * Lifecycle:
 *   1. wsManager dispatches events via handleNotifyEvent()
 *   2. subscribe_success → loadSessions (baseline state)
 *   3. Subsequent events update session state incrementally
 *   4. Never closed (survives page lifetime)
 *   5. On reconnect, subscribe_success fires again → reload baseline
 *
 * Event semantics:
 *   - begin_stream:    set active_stream_id, auto-connect replay
 *   - upsert_session:  create or update session in sidebar (upsert),
 *                      includes active_stream_id for sidebar breathing indicator
 *   - delete_session:  remove session from sidebar
 */
import { useSessionStore } from '@/stores/useSessionStore';
import type { SessionListItem } from '@/types';
import * as StreamManager from '@/stores/streamManager';
import { ts } from '@/utils/logTs';

type NotifyEventType = 'begin_stream' | 'upsert_session' | 'delete_session' | 'apperror';
type NotifyListener = (event: NotifyEventType, data: any) => void;
const _listeners: NotifyListener[] = [];

/**
 * Register a listener for notify events. Returns unsubscribe function.
 * Used by useStreamNotify to react to begin_stream/end_stream for a specific session.
 */
export function addNotifyListener(fn: NotifyListener): () => void {
  _listeners.push(fn);
  return () => {
    const idx = _listeners.indexOf(fn);
    if (idx >= 0) _listeners.splice(idx, 1);
  };
}

function _emit(event: NotifyEventType, data: any) {
  for (const fn of _listeners) {
    try { fn(event, data); } catch { /* listener error non-fatal */ }
  }
}

/**
 * Connect to the global notify SSE. Called once at app startup.
 * On subscribe_success, loads all sessions (baseline).
 * On subsequent events, updates session state incrementally.
 */
// SSE notify connection removed - using WebSocket only

/**
 * Handle a notify event from WebSocket.
 * Called by wsManager when it receives a notify channel message.
 */
export function handleNotifyEvent(event: string, data: any): void {
  console.log(`[${ts()}] [WS/NOTIFY] ${event}:`, data);

  switch (event) {
    case 'subscribe_success':
      // Synchronous baseline: loadSessions() blocks until complete, including
      // subagent sessions with active_stream_id. This ensures the baseline is
      // fully populated before any subsequent begin_stream / upsert_session
      // events are processed.
      useSessionStore.getState().loadSessions();

      // Hardening: clear stale stream states from before disconnection.
      // loadSessions() now returns ALL sessions (including subagents) with
      // active_stream_id, so we can correctly identify stale busy:true entries.
      {
        const sessions = useSessionStore.getState().sessions;
        const activeStreamSessionIds = new Set(
          sessions.filter(s => s.active_stream_id).map(s => s.session_id)
        );
        for (const sid of StreamManager.getActiveSessionIds()) {
          const state = StreamManager.getSessionStreamState(sid);
          if (state?.busy && !activeStreamSessionIds.has(sid)) {
            console.warn(`[notifyManager] Clearing stale stream state for session ${sid}`);
            StreamManager.clearSessionStream(sid);
          }
        }
      }
      break;

    case 'begin_stream':
      // Update session in sidebar
      useSessionStore.setState((state) => ({
        sessions: state.sessions.map((s) =>
          s.session_id === data.session_id
            ? { ...s, active_stream_id: data.stream_id }
            : s
        ),
      }));

      // Auto-connect replay for this stream (via WebSocket)
      // This ensures subagent sessions are tracked in streamManager
      // for breathing animation and real-time message display
      StreamManager.startSessionStream(data.session_id).catch(() => {
        // non-fatal: stream may already be connected
      });

      // Notify listeners (useStreamNotify)
      _emit('begin_stream', data);
      break;

    case 'apperror':
      // Forward to StreamManager's replay handler so the error card displays
      // even if the replay WebSocket was disconnected before draining.
      StreamManager.handleReplayEvent(data.session_id, 'apperror', data);
      break;

    // end_stream removed — 'done' (replay channel) is the sole completion signal.
    // Sidebar breathing indicator is now driven by active_stream_id in upsert_session.

    case 'upsert_session': {
      const existing = useSessionStore.getState().sessions.find(
        (s) => s.session_id === data.session_id
      );

      // Check if delegate children status changed — if so, bump version to trigger sidebar refresh
      const delegateChanged = existing && (
        (data._has_delegate_children !== undefined && data._has_delegate_children !== existing._has_delegate_children) ||
        (data._delegate_children_count !== undefined && data._delegate_children_count !== existing._delegate_children_count)
      );

      if (existing) {
        // Update existing session
        useSessionStore.setState((state) => ({
          sessions: state.sessions.map((s) =>
            s.session_id === data.session_id
              ? {
                  ...s,
                  title: data.title ?? s.title,
                  message_count: data.message_count ?? s.message_count,
                  last_seen_msg_count: data.last_seen_msg_count ?? s.last_seen_msg_count,
                  source_tag: data.source_tag ?? s.source_tag,
                  _parent_session_id: data.parent_session_id ?? s._parent_session_id,
                  // Use active_stream_id from backend data (null = stream ended)
                  active_stream_id: data.active_stream_id !== undefined
                    ? data.active_stream_id
                    : s.active_stream_id,
                  _has_delegate_children: data._has_delegate_children ?? s._has_delegate_children,
                  _delegate_children_count: data._delegate_children_count ?? s._delegate_children_count,
                  _delegate_running_count: data._delegate_running_count ?? s._delegate_running_count,
                }
              : s
          ),
        }));
      } else {
        // Create new session placeholder
        const newSession: SessionListItem = {
          session_id: data.session_id,
          title: data.title || 'New Session',
          message_count: data.message_count ?? 0,
          last_seen_msg_count: data.last_seen_msg_count ?? null,
          created_at: data.ts ?? Date.now() / 1000,
          updated_at: data.ts ?? Date.now() / 1000,
          _parent_session_id: data.parent_session_id ?? undefined,
          source_tag: (data.source_tag ?? undefined) as string | undefined,
          is_cli_session: data.source_tag === 'cli',
          is_subagent_session: data.source_tag === 'subagent',
          pinned: false,
          active_stream_id: data.active_stream_id ?? undefined,
          _has_delegate_children: data._has_delegate_children ?? false,
          _delegate_children_count: data._delegate_children_count ?? 0,
          _delegate_running_count: data._delegate_running_count ?? 0,
        };
        useSessionStore.setState((state) => ({
          sessions: [newSession, ...state.sessions],
        }));
      }

      // Bump version if delegate children status changed to trigger sidebar cache refresh
      if (delegateChanged) {
        useSessionStore.getState().bumpDelegateChildrenVersion();
      }

      // Notify listeners
      _emit('upsert_session', data);
      break;
    }

    case 'delete_session':
      useSessionStore.setState((state) => ({
        sessions: state.sessions.filter((s) => s.session_id !== data.session_id),
      }));
      _emit('delete_session', data);
      break;

    default:
      console.warn(`[${ts()}] [WS/NOTIFY] Unknown event: ${event}`);
  }
}
