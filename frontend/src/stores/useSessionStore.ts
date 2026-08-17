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
  /** Subscribe to Gateway SSE for real-time session list updates. Returns unsubscribe function. */
  subscribeGatewaySSE: () => () => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  sessions: [],
  currentSessionId: null,
  currentSession: null,
  loading: false,
  error: null,
  gatewayOnline: false,
  matchMessageId: null,

  loadSessions: async () => {
    set({ loading: true, error: null });
    try {
      const response = await fetch('/api/sessions', { credentials: 'include' });
      if (!response.ok) throw new Error('Failed to load sessions');
      const data = await response.json();
      const sessions: SessionListItem[] = data.sessions || [];
      // After loading sessions from DB, ensure the active session's title
      // stays in sync between sidebar (sessions[]) and topbar (currentSession).
      const activeId = useSessionStore.getState().currentSessionId;
      const activeSession = useSessionStore.getState().currentSession;
      console.log('[title-sync] loadSessions sync check', { activeId: activeId?.slice(0,8), activeTitle: activeSession?.title, dbTitle: sessions.find(s => s.session_id === activeId)?.title });
      if (activeId && activeSession?.title) {
        for (const s of sessions) {
          if (s.session_id === activeId) {
            console.log('[title-sync] loadSessions syncing sidebar title from currentSession:', activeSession.title);
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
    if (updates.title) {
      console.log('[title-sync] updateCurrentSession called with title:', updates.title, new Error().stack?.split('\n')[2]?.trim());
    }
    set((state) => ({
      currentSession: state.currentSession
        ? { ...state.currentSession, ...updates }
        : null,
    }));
  },

  updateSessionListTitle: (sessionId: string, title: string) => {
    set((state) => {
      const found = state.sessions.find(s => s.session_id === sessionId);
      console.log('[title-sync] updateSessionListTitle', { sessionId, title, found: !!found, sessionCount: state.sessions.length });
      if (!found) {
        console.log('[title-sync] session NOT in list! Available ids:', state.sessions.map(s => s.session_id.slice(0,8)));
      }
      return {
        sessions: state.sessions.map((s) =>
          s.session_id === sessionId ? { ...s, title } : s
        ),
      };
    });
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

  subscribeGatewaySSE: () => {
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;
    // Throttle auto-reload of read-only active sessions: min 5s between reloads
    const _lastReloadTs: Record<string, number> = {};

    const connect = () => {
      if (stopped) return;

      es = new EventSource('/api/sessions/gateway/stream', { withCredentials: true });

      es.addEventListener('open', () => {
        set({ gatewayOnline: true });
      });

      es.addEventListener('sessions_changed', (event) => {
        try {
          const data = JSON.parse(event.data);
          const gatewaySessions: SessionListItem[] = (data.sessions || []).map(
            (s: Record<string, unknown>) => {
              const existing = useSessionStore.getState().sessions.find(
                (ex) => ex.session_id === s.session_id
              );
              return {
                session_id: s.session_id as string,
                title: (s.title as string) || 'Gateway Session',
                workspace: s.workspace as string | undefined,
                model: s.model as string | undefined,
                message_count: s.message_count as number | undefined,
                last_seen_msg_count: (s.last_seen_msg_count ?? existing?.last_seen_msg_count) as number | undefined,
                created_at: s.created_at as number,
                updated_at: s.updated_at as number | undefined,
                pinned: false,
                source_tag: (s.source_tag as string) || (s.source as string) || undefined,
                is_cli_session: true,
                user_id: s.user_id as string | undefined,
                active_stream_id: s.active_stream_id as (string | null | undefined),
              };
            }
          );

          set((state) => {
            // Preserve WebUI-native and cron sessions (SSE only emits cli/messaging),
            // replace cli/messaging gateway sessions.
            const webuiSessions = state.sessions.filter(
              (s) => !s.is_cli_session || s.source_tag === 'cron'
            );
            // Build merged session list from WebUI-native and gateway sessions
            const merged: SessionListItem[] = [
              ...webuiSessions,
              ...gatewaySessions,
            ];
            // Sort by pinned first, then by updated_at desc
            merged.sort((a, b) => {
              if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
              return (b.updated_at || b.created_at) - (a.updated_at || a.created_at);
            });
            return { sessions: merged };
          });

          // Auto-reload read-only active session when message_count increases
          const current = useSessionStore.getState().currentSession;
          const currentId = useSessionStore.getState().currentSessionId;
          if (current?._readonly && currentId) {
            const oldCount = current.message_count ?? 0;
            const gw = gatewaySessions.find((s) => s.session_id === currentId);
            const newCount = gw?.message_count ?? 0;
            if (newCount > oldCount) {
              const lastTs = _lastReloadTs[currentId] || 0;
              const now = Date.now();
              if (now - lastTs >= 5000) {
                _lastReloadTs[currentId] = now;
                useSessionStore.getState().loadSession(currentId).catch(() => {
                  /* ignore — session may have been cleaned up on the agent side */
                });
              }
            }
          }
        } catch (err) {
          console.error('[Gateway SSE] Failed to parse sessions_changed event:', err);
        }
      });

      es.onerror = () => {
        // EventSource auto-closes on error; schedule reconnect
        set({ gatewayOnline: false });
        if (es) {
          es.close();
          es = null;
        }
        if (!stopped) {
          reconnectTimer = setTimeout(connect, 5000);
        }
      };
    };

    connect();

    return () => {
      stopped = true;
      if (es) {
        es.close();
        es = null;
      }
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };
  },
}));
