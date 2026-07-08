import { useEffect, useState, useMemo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import SessionItem from './SessionItem';

export default function SessionList() {
  const { t } = useTranslation('common');
  const { sessions, currentSessionId, currentSession, loadSessions, createSession, selectSession, gatewayOnline } = useSessionStore();
  const { workspaces } = useWorkspaceStore();
  const [searchQuery, setSearchQuery] = useState('');
  // Set of collapsed workspace keys; first group is expanded by default (not in set)
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(new Set());
  // Track whether we have initialized collapse state from the first render
  const [collapseInit, setCollapseInit] = useState(false);
  // Cleanup modal state
  const [showCleanupModal, setShowCleanupModal] = useState(false);
  const [cleanupSource, setCleanupSource] = useState('');
  const [cleanupDays, setCleanupDays] = useState(30);
  const [cleanupPreview, setCleanupPreview] = useState<number | null>(null);
  const [cleanupRunning, setCleanupRunning] = useState(false);

  const showCliSessions = useSettingsStore((s) => s.settings?.show_cli_sessions ?? false);

  // Load sessions on mount
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // Subscribe to Gateway SSE for real-time session updates
  useEffect(() => {
    if (!showCliSessions) return;
    const unsubscribe = useSessionStore.getState().subscribeGatewaySSE();
    return unsubscribe;
  }, [showCliSessions]);

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

  // Build map of parent → archived children (read-only compaction snapshots)
  const archivedMap = useMemo(() => {
    const map = new Map<string, typeof sessions>();
    filteredSessions.forEach((s) => {
      if (s._readonly && s._parent_session_id) {
        const children = map.get(s._parent_session_id) || [];
        children.push(s);
        map.set(s._parent_session_id, children);
      }
    });
    return map;
  }, [filteredSessions]);



  // All sessions (WebUI + gateway/CLI), excluding archived compaction snapshots
  const allSessions = useMemo(
    () => filteredSessions.filter((s) => !(s._readonly && s._parent_session_id)),
    [filteredSessions]
  );

  // Group sessions by workspace; pinned sessions stay at the top within their workspace group.
  const groupedSessions = useMemo(() => {
    const groups: { label: string; sessions: typeof sessions; key: string }[] = [];

    // Map from workspace path (or '__none__') → session list (all sessions, including pinned)
    const wsMap = new Map<string, typeof sessions>();

    allSessions.forEach((s) => {
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
  }, [allSessions, wsLabel, t]);

  // Initialize collapse state once groups are known:
  // first group expanded, all others collapsed
  useEffect(() => {
    if (collapseInit || groupedSessions.length === 0) return;
    const allGroupKeys = groupedSessions.map((g) => g.key);
    // First group expanded, all others collapsed
    setCollapsedKeys(new Set(allGroupKeys.slice(1)));
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

  const handleCleanupOpen = (sourceTag?: string) => {
    setCleanupSource(sourceTag || '');
    setCleanupDays(30);
    setCleanupPreview(null);
    setShowCleanupModal(true);
  };

  const handleCleanupClose = () => {
    setShowCleanupModal(false);
    setCleanupPreview(null);
  };

  const handleCleanupConfirm = async () => {
    setCleanupRunning(true);
    try {
      const res = await fetch('/api/sessions/cleanup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: cleanupSource, older_than_days: cleanupDays }),
        credentials: 'include',
      });
      const data = await res.json();
      if (res.ok) {
        setCleanupPreview(data.deleted_count);
        // Reload sessions after cleanup
        await loadSessions();
      } else {
        alert(data.error || 'Cleanup failed');
      }
    } catch (err) {
      alert(`Cleanup failed: ${(err as Error).message}`);
    } finally {
      setCleanupRunning(false);
      setShowCleanupModal(false);
    }
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
        {groupedSessions.length > 0 && (
          <div className="webui-sessions-section">
            <div className="flex items-center justify-between px-2 py-1">
              {showCliSessions && (
                <button
                  className="text-xs text-[var(--muted)] hover:text-[var(--accent)] transition-colors"
                  onClick={() => handleCleanupOpen()}
                  title="Clean up old gateway sessions"
                >
                  🧹 Clean
                </button>
              )}
              {showCliSessions && !gatewayOnline && <span className="text-xs text-[var(--accent)]" title="Gateway offline">⚠</span>}
            </div>
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
          </div>
        )}

        {groupedSessions.length === 0 && archivedMap.size === 0 && (
          <div className="text-center text-[var(--muted)] text-sm py-4">
            {searchQuery ? t('sessions.no_results') : t('sessions.empty')}
          </div>
        )}
      </div>

      {/* Cleanup Modal */}
      {showCleanupModal && (
        <div className="clear-history-overlay" onClick={handleCleanupClose}>
          <div className="clear-history-modal" onClick={(e) => e.stopPropagation()}>
            <div className="clear-history-modal__title">Clean Up Sessions</div>
            <div className="clear-history-modal__desc">
              Delete ended sessions older than the selected period.
              Pinned sessions are never deleted.
            </div>
            <div className="clear-history-modal__field">
              <label className="clear-history-modal__label">Source</label>
              <select
                className="clear-history-modal__input"
                value={cleanupSource}
                onChange={(e) => setCleanupSource(e.target.value)}
              >
                <option value="">-- Select --</option>
                <option value="webui">Web UI</option>
                {(() => [...new Set(filteredSessions.filter(s => s.is_cli_session).map(s => s.source_tag).filter(Boolean) as string[])].sort())().map((tag) => (
                  <option key={tag} value={tag}>{tag}</option>
                ))}
              </select>
            </div>
            <div className="clear-history-modal__field">
              <label className="clear-history-modal__label">Older Than</label>
              <select
                className="clear-history-modal__input"
                value={cleanupDays}
                onChange={(e) => setCleanupDays(Number(e.target.value))}
              >
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
              </select>
            </div>
            {cleanupPreview !== null && (
              <div className="text-sm text-green-500 mb-2">Deleted {cleanupPreview} session(s).</div>
            )}
            <div className="clear-history-modal__actions">
              <button className="clear-history-modal__btn clear-history-modal__btn--cancel" onClick={handleCleanupClose}>
                Cancel
              </button>
              <button
                className="clear-history-modal__btn clear-history-modal__btn--confirm"
                onClick={handleCleanupConfirm}
                disabled={!cleanupSource || cleanupRunning}
              >
                {cleanupRunning ? 'Cleaning...' : 'Clean Up'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
