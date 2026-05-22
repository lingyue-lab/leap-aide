import { create } from 'zustand';
import type { Session, SessionListItem } from '@/types';

interface SessionState {
  sessions: SessionListItem[];
  currentSessionId: string | null;
  currentSession: Session | null;
  loading: boolean;
  error: string | null;
  
  // Actions
  loadSessions: () => Promise<void>;
  createSession: (workspace?: string) => Promise<Session>;
  selectSession: (id: string | null) => void;
  loadSession: (id: string) => Promise<void>;
  renameSession: (id: string, title: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  pinSession: (id: string, pinned: boolean) => Promise<void>;
  clearSession: (id: string) => Promise<void>;
  updateCurrentSession: (session: Partial<Session>) => void;
  /** Update only the title in the sidebar session list (for live title push from StreamManager) */
  updateSessionListTitle: (sessionId: string, title: string) => void;
  /** Assign (or switch) the workspace path for a session */
  assignWorkspace: (id: string, workspace: string) => Promise<void>;
  /** Update the skill binding list for a session */
  updateSessionSkills: (id: string, skills: string[] | null) => Promise<void>;
  /** Update the model for a specific session */
  updateSessionModel: (id: string, model: string) => Promise<void>;
}

export const useSessionStore = create<SessionState>((set) => ({
  sessions: [],
  currentSessionId: null,
  currentSession: null,
  loading: false,
  error: null,

  loadSessions: async () => {
    set({ loading: true, error: null });
    try {
      const response = await fetch('/api/sessions', { credentials: 'include' });
      if (!response.ok) throw new Error('Failed to load sessions');
      const data = await response.json();
      const sessions: SessionListItem[] = data.sessions || [];
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
      set((state) => ({
        sessions: [
          {
            session_id: session.session_id,
            title: session.title,
            created_at: session.created_at,
            pinned: false,
            workspace: session.workspace,
          },
          ...state.sessions,
        ],
        currentSessionId: session.session_id,
        currentSession: session,
      }));
      return session;
    } catch (err) {
      throw err;
    }
  },

  selectSession: (id) => {
    set({ currentSessionId: id });
  },

  loadSession: async (id) => {
    set({ loading: true, error: null });
    try {
      const response = await fetch(`/api/session?session_id=${encodeURIComponent(id)}`, {
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to load session');
      const { session } = await response.json();
      set({
        currentSession: session,
        currentSessionId: id,
        loading: false,
      });
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
}));
