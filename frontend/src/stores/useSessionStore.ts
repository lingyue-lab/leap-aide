import { create } from 'zustand';
import type { Session, SessionListItem } from '@/types';

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
}

export const useSessionStore = create<SessionState>((set) => ({
  sessions: [],
  currentSessionId: null,
  currentSession: null,
  loading: false,
  error: null,
  gatewayOnline: false,
  matchMessageId: null,
  delegateChildrenVersion: 0,

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
    try {
      const response = await fetch('/api/session/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(workspace ? { workspace } : {}),
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to create session');
      const { session } = await response.json();
      const sessionItem = {
        session_id: session.session_id,
        title: session.title,
        created_at: session.created_at,
        pinned: false,
        workspace: session.workspace,
      };
      set((state) => {
        // Deduplicate: WebSocket upsert_session may have already created a
        // placeholder for this session.  Update it in-place instead of adding
        // a second entry.
        const exists = state.sessions.some((s) => s.session_id === session.session_id);
        return {
          sessions: exists
            ? state.sessions.map((s) => (s.session_id === session.session_id ? { ...s, ...sessionItem } : s))
            : [sessionItem, ...state.sessions],
          currentSessionId: session.session_id,
          currentSession: session,
        };
      });
      return session;
    } catch (err) {
      throw err;
    }
  },

  selectSession: (id, matchMessageId = null) => {
    set({ currentSessionId: id, matchMessageId });
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
    } catch (err) {
      throw err;
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
