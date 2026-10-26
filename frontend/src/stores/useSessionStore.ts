import { create } from 'zustand';
import type { Session, SessionListItem } from '@/types';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

interface SessionState {
  sessions: SessionListItem[];
  currentSessionId: string | null;
  currentSession: Session | null;
  loading: boolean;
  error: string | null;
  gatewayOnline: boolean;
  /** DB rowid of the search-hit message to scroll to after session loads */
  matchMessageId: number | null;
  /** Bumped on every delegate_children_changed SSE event so the sidebar can
   *  refresh its cached delegate-children list (dropping stale child IDs). */
  delegateChildrenVersion: number;
  /** Placeholder Session shown when the user clicked `+` but hasn't sent the
   *  first message yet. Lives only in the frontend store — no backend call
   *  happens until the user actually sends a message, at which point the
   *  backend creates the real session and `promotePendingToReal` swaps it in.
   *  Drives the welcome-state rendering branch in MainContent. */
  pendingNewSession: Session | null;
  /** B.6 — true while the welcome-state placeholder is being promoted to a
   *  real session (POST /api/chat/start round-trip + StreamManager reconnect).
   *  Drives the Loading Spinner in WelcomeState. */
  pendingNewSessionPromoting: boolean;
  /** Session id currently being pinned/unpinned. Non-null while a
   *  pinSession() promise is in flight. The server-side /api/session/pin
   *  handler synchronously boots SessionDisplay (Xvfb + dbus + fcitx5 +
   *  openbox + x11vnc + Chrome), which takes 10-15s on cold start.
   *  Drives the fullscreen PinningOverlay so the user sees feedback that
   *  the click registered. Cleared in a finally block so a server error
   *  cannot leave the overlay stuck. */
  pinningSession: string | null;

  // Actions
  loadSessions: () => Promise<void>;
  createSession: (workspace?: string) => Promise<Session>;
  selectSession: (id: string | null, matchMessageId?: number | null) => void;
  loadSession: (id: string) => Promise<void>;
  renameSession: (id: string, title: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  pinSession: (id: string, pinned: boolean) => Promise<void>;
  clearSession: (id: string) => Promise<void>;
  updateCurrentSession: (session: Partial<Session>) => void;
  /** Update only the title in the sidebar session list (for live title push from StreamManager) */
  updateSessionListTitle: (sessionId: string, title: string) => void;
  /** Update last_seen_msg_count in the sidebar session list (optimistic update after markSeen) */
  updateSessionLastSeen: (sessionId: string, count: number) => void;
  /** Assign (or switch) the workspace path for a session */
  assignWorkspace: (id: string, workspace: string) => Promise<void>;
  /** Update the skill binding list for a session */
  updateSessionSkills: (id: string, skills: string[] | null) => Promise<void>;
  /** Update the model for a specific session */
  updateSessionModel: (id: string, model: string) => Promise<void>;
  /** Update the reasoning effort for a specific session */
  updateSessionReasoningEffort: (id: string, effort: string | null) => Promise<void>;
  /** Update the personality for a specific session */
  updateSessionPersonality: (id: string, personality: string | null) => Promise<void>;
  /** Bump delegateChildrenVersion to trigger sidebar refresh of delegate children cache */
  bumpDelegateChildrenVersion: () => void;
  /** Enter welcome state: build a local placeholder Session, set as active,
   *  but do NOT touch `sessions[]` (sidebar stays empty of this row). */
  enterPendingNewSession: (workspace?: string) => void;
  /** Exit welcome state without replacing currentSession — used when canceling
   *  or when an external navigation clears the placeholder. */
  exitPendingNewSession: () => void;
  /** Welcome → active swap: backend has materialized the real session; insert
   *  it at the top of `sessions[]`, replace the placeholder currentSession,
   *  clear pendingNewSession. Mirrors currentSession per CLAUDE.md rule #8. */
  promotePendingToReal: (realSession: Session) => void;
  /** B.6 — flip the promoting flag; WelcomeState spinner shows while true. */
  setPendingNewSessionPromoting: (v: boolean) => void;
}

export const useSessionStore = create<SessionState>((set, get) => ({
  sessions: [],
  currentSessionId: null,
  currentSession: null,
  loading: false,
  error: null,
  gatewayOnline: false,
  matchMessageId: null,
  delegateChildrenVersion: 0,
  pendingNewSession: null,
  pendingNewSessionPromoting: false,
  pinningSession: null,

  loadSessions: async () => {
    set({ loading: true, error: null });
    try {
      // Synchronous XHR: blocks until response arrives. This ensures the
      // baseline state is fully populated before any subsequent notify events
      // (begin_stream, upsert_session) are processed. The brief UI freeze
      // (~100-300ms) only occurs once per WebSocket (re)connection.
      const xhr = new XMLHttpRequest();
      xhr.open('GET', '/api/sessions', false); // false = synchronous
      xhr.withCredentials = true;
      xhr.send();
      if (xhr.status !== 200) throw new Error('Failed to load sessions');
      const data = JSON.parse(xhr.responseText);
      const sessions: SessionListItem[] = data.sessions || [];
      // After loading sessions from DB, ensure the active session's title
      // stays in sync between sidebar (sessions[]) and topbar (currentSession).
      const activeId = useSessionStore.getState().currentSessionId;
      const activeSession = useSessionStore.getState().currentSession;
      if (activeId && activeSession?.title) {
        for (const s of sessions) {
          if (s.session_id === activeId) {
            s.title = activeSession.title;
            break;
          }
        }
      }
      set({ sessions, loading: false });
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  createSession: async (workspace?: string) => {
    // Legacy entry point kept for backward compat (SessionList.handleNewChat
    // already awaits this).  The new flow goes through enterPendingNewSession
    // directly — no backend call until the user sends the first message.
    get().enterPendingNewSession(workspace);
    // Return a synthetic placeholder so callers awaiting the original Session
    // shape don't NPE.  It is replaced by the real session via
    // promotePendingToReal on first send.
    return get().currentSession as Session;
  },

  enterPendingNewSession: (workspace?: string) => {
    const state = get();
    // no-op: user already in welcome state (matches the no-op-on-+-again decision).
    if (state.pendingNewSession) return;

    // Placeholder sid is just a local handle — backend will mint its own real
    // session_id on first send (see api/routes.py:_handle_chat_start auto-create
    // branch).  Format is arbitrary; the `new-local-` prefix only helps log triage.
    const placeholderSid = `new-local-${crypto.randomUUID()}`;
    // Default the placeholder's model to the user's settings (loadSettings
    // runs once at app boot, so settings?.model is reliably populated by the
    // time any + click happens).  Fallback matches defaultSettings.model in
    // useSettingsStore.ts.  Without this the composer footer shows no
    // selected model in welcome state.
    const defaultModel = useSettingsStore.getState().settings?.model;
    // New sessions default to the built-in Default workspace — no
    // inheritance from the current session (workspace ownership lives on
    // state.db cwd; the WebUI's only default is builtin Default, issue #7).
    // Falls back to undefined → backend new_session() resolves Default.
    const builtinDefaultWs = useWorkspaceStore.getState().workspaces
      .find((w) => w.builtin)?.path;
    const placeholder: Session = {
      session_id: placeholderSid,
      title: 'Untitled',
      workspace: workspace || builtinDefaultWs || undefined,
      model: state.currentSession?.model ?? defaultModel ?? 'anthropic/claude-sonnet-4-5',
      reasoning_effort: null,
      personality: null,
      pinned: false,
      messages: [],
      created_at: Date.now() / 1000,
    } as Session;

    set({
      currentSessionId: placeholderSid,
      currentSession: placeholder,
      pendingNewSession: placeholder,
    });
    // ── Reset chatStore so the compose footer shows defaults in welcome state ──
    // enterPendingNewSession is the single entry point for entering welcome
    // state (called from both sidebar '+' and WelcomeState's mount effect).
    // The no-op guard above ensures we only reset on a real transition.
    // StreamManager's _sessionStates and _sessionCache are untouched, so any
    // in-flight stream on the previous session and any cached session data
    // survive and can be restored when the user navigates back.
    useChatStore.getState().reset();
    // Note: sessions[] intentionally NOT touched (per requirement "sidebar不动").
  },

  exitPendingNewSession: () => {
    set({ pendingNewSession: null, pendingNewSessionPromoting: false });
  },

  promotePendingToReal: (realSession: Session) => {
    set((state) => {
      const item: SessionListItem = {
        session_id: realSession.session_id,
        title: realSession.title,
        created_at: realSession.created_at,
        pinned: realSession.pinned ?? false,
        workspace: realSession.workspace,
        // Carry active_stream_id from the POST /api/chat/start response
        // (api/routes.py:4213). The backend fires `begin_stream` notify
        // synchronously before the HTTP response, so the notify handler in
        // notifyManager.ts arrives BEFORE this promotion runs and finds no
        // session to update in `sessions[]` (placeholder hasn't promoted
        // yet) — leaving active_stream_id undefined on the sidebar row.
        // Without it, the breathing pulse only shows while the new session
        // is the active one (chatStore.activeStreamId set); selecting any
        // other session hides it for the rest of the stream. Copying the
        // field here restores the intended behavior: pulse shows whenever
        // the session is streaming, regardless of selection.
        active_stream_id: realSession.active_stream_id ?? undefined,
      };
      // Dedupe: same as createSession path — the WebSocket upsert_session
      // event for the freshly-created session may have already pre-inserted
      // a placeholder before our HTTP response arrived.
      const exists = state.sessions.some((s) => s.session_id === realSession.session_id);
      return {
        sessions: exists
          ? state.sessions.map((s) => (s.session_id === realSession.session_id ? { ...s, ...item } : s))
          : [item, ...state.sessions],
        pendingNewSession: null,
        pendingNewSessionPromoting: false,
        currentSessionId: realSession.session_id,
        currentSession: realSession,
      };
    });
  },

  setPendingNewSessionPromoting: (v: boolean) => {
    set({ pendingNewSessionPromoting: v });
  },

  selectSession: (id, matchMessageId = null) => {
    // If we're in welcome state (placeholder Session with no real session yet)
    // and the user clicks a sidebar session, exit welcome so the chat view
    // actually switches.  Without this guard, NonTerminalContent short-circuits
    // on pendingNewSession and the chat view never updates — looks like the
    // click had no response.
    set((state) => {
      if (state.pendingNewSession) {
        return {
          currentSessionId: id,
          matchMessageId,
          pendingNewSession: null,
          // currentSession is left as the placeholder — ChatInterface's
          // session switch useEffect will call loadSession(id) which
          // replaces it with the real one via the stale-guard.
        };
      }
      return { currentSessionId: id, matchMessageId };
    });
  },

  loadSession: async (id) => {
    set({ loading: true, error: null });
    try {
      const response = await fetch(`/api/session?session_id=${encodeURIComponent(id)}`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to load session');
      const { session } = await response.json();
      // Only update currentSessionId if it still matches what we loaded.
      // This prevents a race where the user switched away while the request
      // was in-flight, and the late response would snap back to the old session.
      set((state) => ({
        // Guard currentSession the same way as currentSessionId: a late response
        // for a session the user already navigated away from must not overwrite
        // the currently-displayed session (this was the root cause of stale
        // messages/date-separators leaking across sessions on rapid switching).
        currentSession: state.currentSessionId === id ? session : state.currentSession,
        currentSessionId: state.currentSessionId === id ? id : state.currentSessionId,
        loading: false,
      }));
      // Sync the loaded title into the sidebar list so the sidebar and
      // topbar never diverge (especially after async LLM title generation).
      if (session?.title) {
        set((state) => ({
          sessions: state.sessions.map((s) =>
            s.session_id === id ? { ...s, title: session.title } : s
          ),
        }));
      }
    } catch (err) {
      set({ error: (err as Error).message, loading: false });
    }
  },

  renameSession: async (id, title) => {
    try {
      await fetch('/api/session/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: id, title }),
        credentials: 'include',
      });
      set((state) => ({
        sessions: state.sessions.map((s) =>
          s.session_id === id ? { ...s, title } : s
        ),
        currentSession: state.currentSession?.session_id === id
          ? { ...state.currentSession, title }
          : state.currentSession,
      }));
    } catch (err) {
      throw err;
    }
  },

  deleteSession: async (id) => {
    try {
      await fetch('/api/session/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: id }),
        credentials: 'include',
      });
      set((state) => {
        const sessions = state.sessions.filter((s) => s.session_id !== id);
        const currentSessionId = state.currentSessionId === id ? null : state.currentSessionId;
        return {
          sessions,
          currentSessionId,
          currentSession: currentSessionId === null ? null : state.currentSession,
        };
      });
    } catch (err) {
      throw err;
    }
  },

  pinSession: async (id, pinned) => {
    // Mark this session as pinning so the fullscreen PinningOverlay shows
    // during the 10-15s server-side SessionDisplay.start() round-trip.
    // The flag is cleared in finally so a server error cannot leave the
    // overlay stuck on screen. Only one pin is in flight at a time by
    // design (clicking pin on session B while A is still pinning will
    // queue behind A's overlay).
    set({ pinningSession: id });
    try {
      await fetch('/api/session/pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: id, pinned }),
        credentials: 'include',
      });
      set((state) => ({
        sessions: state.sessions.map((s) =>
          s.session_id === id ? { ...s, pinned } : s
        ),
        // Mirror pinned into currentSession so consumers reading
        // currentSession.pinned (e.g. MainContent's observe button) re-render
        // immediately, without requiring a session switch round-trip.
        currentSession:
          state.currentSession?.session_id === id
            ? { ...state.currentSession, pinned }
            : state.currentSession,
      }));
    } finally {
      set({ pinningSession: null });
    }
  },

  clearSession: async (id) => {
    try {
      await fetch('/api/session/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: id }),
        credentials: 'include',
      });
      set((state) => ({
        currentSession: state.currentSession?.session_id === id
          ? { ...state.currentSession, messages: [] }
          : state.currentSession,
      }));
    } catch (err) {
      throw err;
    }
  },

  updateCurrentSession: (updates) => {
    set((state) => ({
      currentSession: state.currentSession
        ? { ...state.currentSession, ...updates }
        : null,
    }));
  },

  updateSessionListTitle: (sessionId: string, title: string) => {
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === sessionId ? { ...s, title } : s
      ),
      // Mirror title into currentSession so topbar (which reads
      // currentSession.title directly) reflects streaming-generated titles
      // without requiring a session switch round-trip.
      currentSession:
        state.currentSession?.session_id === sessionId
          ? { ...state.currentSession, title }
          : state.currentSession,
    }));
  },

  updateSessionLastSeen: (sessionId: string, count: number) => {
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === sessionId ? { ...s, last_seen_msg_count: count } : s
      ),
    }));
  },

  assignWorkspace: async (id, workspace) => {
    // Welcome-state placeholder: this session exists only on the client — the
    // backend mints the real id on the first /api/chat/start. POSTing here
    // would 404 ("Session not found") and leave the chip stuck on its previous
    // value. Keep the pick in the local placeholder instead; WelcomeState
    // forwards currentSession.workspace as body.workspace on the first send,
    // so the real session is created with it (issue #4).
    const pending = get().pendingNewSession;
    if (pending && pending.session_id === id) {
      set((state) => ({
        currentSession:
          state.currentSession?.session_id === id
            ? { ...state.currentSession, workspace }
            : state.currentSession,
        pendingNewSession: { ...pending, workspace },
      }));
      return;
    }
    const response = await fetch('/api/session/workspace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id, workspace }),
      credentials: 'include',
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error((data as { error?: string }).error || 'Failed to assign workspace');
    }
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === id ? { ...s, workspace } : s
      ),
      currentSession:
        state.currentSession?.session_id === id
          ? { ...state.currentSession, workspace }
          : state.currentSession,
    }));
  },

  updateSessionSkills: async (id, skills) => {
    const response = await fetch('/api/session/skills', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id, skills: skills ?? [] }),
      credentials: 'include',
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error((data as { error?: string }).error || 'Failed to update session skills');
    }
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === id ? { ...s, skills } : s
      ),
      currentSession:
        state.currentSession?.session_id === id
          ? { ...state.currentSession, skills }
          : state.currentSession,
    }));
  },

  updateSessionModel: async (id, model) => {
    const response = await fetch('/api/session/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id, model }),
      credentials: 'include',
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error((data as { error?: string }).error || 'Failed to update session model');
    }
    set((state) => ({
      currentSession:
        state.currentSession?.session_id === id
          ? { ...state.currentSession, model }
          : state.currentSession,
    }));
  },

  updateSessionReasoningEffort: async (id, effort) => {
    const response = await fetch('/api/session/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id, reasoning_effort: effort }),
      credentials: 'include',
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error((data as { error?: string }).error || 'Failed to update reasoning effort');
    }
    set((state) => ({
      currentSession:
        state.currentSession?.session_id === id
          ? { ...state.currentSession, reasoning_effort: effort }
          : state.currentSession,
    }));
  },

  updateSessionPersonality: async (id, personality) => {
    const response = await fetch('/api/personality/set', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: id, name: personality ?? '' }),
      credentials: 'include',
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error((data as { error?: string }).error || 'Failed to update session personality');
    }
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.session_id === id ? { ...s, personality } : s
      ),
      currentSession:
        state.currentSession?.session_id === id
          ? { ...state.currentSession, personality }
          : state.currentSession,
    }));
  },

  /** Bump delegateChildrenVersion to trigger sidebar refresh of delegate children cache */
  bumpDelegateChildrenVersion: () => {
    set((state) => ({ delegateChildrenVersion: state.delegateChildrenVersion + 1 }));
  },
}));
