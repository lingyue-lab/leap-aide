import { useEffect, useState, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import SessionItem from './SessionItem';

export default function SessionList() {
  const { t } = useTranslation('common');
  const { sessions, currentSessionId, currentSession, loadSessions, createSession, selectSession } = useSessionStore();
  const { workspaces } = useWorkspaceStore();
  const [searchQuery, setSearchQuery] = useState('');
  // Set of collapsed workspace keys; first group is expanded by default (not in set)
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(new Set());
  // Track whether we have initialized collapse state from the first render
  const [collapseInit, setCollapseInit] = useState(false);

  // Load sessions on mount
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // Auto-select first session on initial load (e.g. after page refresh or restart).
  // Pinned sessions sort first, so they get priority.
  const [autoSelectDone, setAutoSelectDone] = useState(false);
  useEffect(() => {
    if (autoSelectDone) return;
    if (sessions.length > 0 && !currentSessionId) {
      setAutoSelectDone(true);
      selectSession(sessions[0].session_id);
    }
  }, [sessions, currentSessionId, autoSelectDone, selectSession]);

  // Filter sessions by search query
  const filteredSessions = useMemo(() => {
    const sessionList = sessions || [];
    if (!searchQuery.trim()) return sessionList;
    const query = searchQuery.toLowerCase();
    return sessionList.filter((s) => s.title.toLowerCase().includes(query));
  }, [sessions, searchQuery]);

  // Resolve a workspace path to a display label
  const wsLabel = useMemo(() => {
    const map = new Map<string, string>();
    workspaces.forEach((ws) => {
      map.set(ws.path, ws.display_name || ws.name);
    });
    return (path: string | undefined): string => {
      if (!path) return t('sessions.no_workspace') || 'No Workspace';
      if (map.has(path)) return map.get(path)!;
      // Fallback: last segment of the path
      return path.split('/').filter(Boolean).pop() || path;
    };
  }, [workspaces, t]);

  // Separate archived (read-only compaction snapshots) from active sessions
  const { topLevelSessions, archivedMap } = useMemo(() => {
    const archivedMap = new Map<string, typeof sessions>();
    const topLevel = filteredSessions.filter((s) => {
      if (s._readonly && s._parent_session_id) {
        const children = archivedMap.get(s._parent_session_id) || [];
        children.push(s);
        archivedMap.set(s._parent_session_id, children);
        return false;
      }
      return true;
    });
    return { topLevelSessions: topLevel, archivedMap };
  }, [filteredSessions]);

  // Group sessions by workspace; pinned sessions stay at the top within their workspace group.
  const groupedSessions = useMemo(() => {
    const groups: { label: string; sessions: typeof sessions; key: string }[] = [];

    // Map from workspace path (or '__none__') → session list (all sessions, including pinned)
    const wsMap = new Map<string, typeof sessions>();

    topLevelSessions.forEach((s) => {
      const key = s.workspace || '__none__';
      if (!wsMap.has(key)) wsMap.set(key, []);
      wsMap.get(key)!.push(s);
    });

    // Sort workspace groups alphabetically by label (stable, deterministic order)
    const wsEntries = Array.from(wsMap.entries()).sort((a, b) => {
      const labelA = wsLabel(a[0] === '__none__' ? undefined : a[0]);
      const labelB = wsLabel(b[0] === '__none__' ? undefined : b[0]);
      return labelA.localeCompare(labelB);
    });

    wsEntries.forEach(([wsPath, wsSessions]) => {
      // Within each workspace: pinned first, then by created_at descending
      const sorted = [...wsSessions].sort((a, b) => {
        if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
        return b.created_at - a.created_at;
      });
      groups.push({
        label: wsLabel(wsPath === '__none__' ? undefined : wsPath),
        sessions: sorted,
        key: `ws:${wsPath}`,
      });
    });

    return groups;
  }, [topLevelSessions, wsLabel, t]);

  // Initialize collapse state once groups are known:
  // first group expanded, all others collapsed
  useEffect(() => {
    if (collapseInit || groupedSessions.length === 0) return;
    setCollapsedKeys(new Set(groupedSessions.slice(1).map((g) => g.key)));
    setCollapseInit(true);
  }, [groupedSessions, collapseInit]);

  // When search is active, expand all groups so results are visible
  useEffect(() => {
    if (searchQuery.trim()) {
      setCollapsedKeys(new Set());
    }
  }, [searchQuery]);

  const toggleGroup = useCallback((key: string) => {
    setCollapsedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const handleNewChat = async () => {
    // Inherit workspace from the current active session; fallback to Home (undefined → backend default)
    const activeWs = sessions.find((s) => s.session_id === currentSessionId)?.workspace
      || currentSession?.workspace;
    await createSession(activeWs || undefined);
  };

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value);
  };

  return (
    <>
      <div className="sidebar-section">
        <button className="new-chat-btn" onClick={handleNewChat}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span>{t('sessions.new_conversation')}</span>
          <span className="text-xs opacity-50 ml-1">Cmd+K</span>
        </button>
      </div>
      
      <div className="session-search">
        <input
          type="text"
          placeholder={t('sessions.filter')}
          value={searchQuery}
          onChange={handleSearchChange}
        />
      </div>
      
      <div className="session-list">
        {groupedSessions.map((group) => {
          const collapsed = collapsedKeys.has(group.key);
          return (
            <div key={group.key}>
              <div
                className={`session-ws-header${collapsed ? ' collapsed' : ''}`}
                onClick={() => toggleGroup(group.key)}
              >
                <svg
                  className="session-ws-caret"
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
                <span className="session-ws-label">{group.label}</span>
                <span className="session-ws-count">{group.sessions.length}</span>
              </div>
              {!collapsed && group.sessions.map((session) => (
                <SessionItem
                  key={session.session_id}
                  session={session}
                  isActive={currentSessionId === session.session_id}
                  onClick={() => selectSession(session.session_id)}
                  archivedChildren={archivedMap.get(session.session_id)}
                />
              ))}
            </div>
          );
        })}
        
        {topLevelSessions.length === 0 && archivedMap.size === 0 && (
          <div className="text-center text-[var(--muted)] text-sm py-4">
            {searchQuery ? t('sessions.no_results') : t('sessions.empty')}
          </div>
        )}
      </div>
    </>
  );
}
