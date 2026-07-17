import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
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
  const [searchResults, setSearchResults] = useState<any[] | null>(null);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const DEBOUNCE_MS = 280;

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

  // Filter sessions: server-side search when searchResults is set, else client-side fallback
  const filteredSessions = useMemo(() => {
    const sessionList = sessions || [];
    // Use server search results if available
    if (searchResults !== null) return searchResults;
    // Client-side fallback (no search active)
    if (!searchQuery.trim()) return sessionList;
    const query = searchQuery.toLowerCase();
    return sessionList.filter((s) => s.title.toLowerCase().includes(query));
  }, [sessions, searchQuery, searchResults]);

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

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const query = e.target.value;
    setSearchQuery(query);

    // Clear pending debounce
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }

    // Clear search results when input is empty
    if (!query.trim()) {
      setSearchResults(null);
      setSearching(false);
      return;
    }

    // Debounce the API call
    setSearching(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await fetch(
          `/api/sessions/search?q=${encodeURIComponent(query)}&content=1&depth=0`,
          { credentials: 'include' }
        );
        const data = await res.json();
        if (data.sessions) {
          // Merge with existing session data for workspace/pinned/etc
          const sessionMap = new Map((sessions || []).map(s => [s.session_id, s]));
          const merged = data.sessions.map((sr: any) => {
            const existing = sessionMap.get(sr.session_id);
            return existing ? { ...existing, ...sr } : sr;
          });
          setSearchResults(merged);
        }
      } catch {
        // Fall back to client-side filtering on error
        setSearchResults(null);
      } finally {
        setSearching(false);
      }
    }, DEBOUNCE_MS);
  }, [sessions]);

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
      {/* Sidebar toolbar — single-row header */}
      <div className="sidebar-toolbar">
        {/* Search — expandable input */}
        <div className="sidebar-toolbar-search">
          <svg className="sidebar-toolbar-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            placeholder={t('sessions.filter')}
            value={searchQuery}
            onChange={handleSearchChange}
          />
          {searchQuery && (
            <button
              className="sidebar-toolbar-search-clear"
              onClick={() => { setSearchQuery(''); setSearchResults(null); }}
              aria-label="Clear search"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18"/>
                <line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          )}
        </div>

        {/* Secondary actions */}
        <div className="sidebar-toolbar-actions">
          {showCliSessions && (
            <button
              className="sidebar-toolbar-btn"
              onClick={() => handleCleanupOpen()}
              title="Clean up old gateway sessions"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
            </button>
          )}
          {showCliSessions && !gatewayOnline && (
            <span className="sidebar-toolbar-warn" title="Gateway offline">⚠</span>
          )}
          {/* New Conversation — placed right of cleanup */}
          <button
            className="sidebar-toolbar-btn sidebar-toolbar-btn--primary"
            onClick={handleNewChat}
            title={t('sessions.new_conversation') + ' (Cmd+K)'}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        </div>
      </div>

      <div className="session-list">
        {/* Search results header */}
        {searchResults !== null && (
          <div style={{ padding: '6px 10px', fontSize: 11, color: 'var(--muted)' }}>
            {searching ? (
              'Searching…'
            ) : (
              `${searchResults.length} result${searchResults.length !== 1 ? 's' : ''} for "${searchQuery}"`
            )}
          </div>
        )}

        {/* No matching results */}
        {searchResults !== null && searchResults.length === 0 && !searching && (
          <div className="text-center text-[var(--muted)] text-sm py-4">
            No matching conversations
          </div>
        )}

        {/* Search active: flat list without workspace grouping */}
        {searchResults !== null && searchResults.length > 0 && (
          <div className="webui-sessions-section">
            {searchResults.map((session) => (
              <SessionItem
                key={session.session_id}
                session={session}
                isActive={currentSessionId === session.session_id}
                onClick={() => selectSession(session.session_id, (session as any).match_message_id ?? null)}
                archivedChildren={archivedMap.get(session.session_id)}
              />
            ))}
          </div>
        )}

        {/* Normal grouped view (when not searching) */}
        {searchResults === null && groupedSessions.length > 0 && (
          <div className="webui-sessions-section">
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
                      onClick={() => selectSession(session.session_id, (session as any).match_message_id ?? null)}
                      archivedChildren={archivedMap.get(session.session_id)}
                    />
                  ))}
                </div>
              );
            })}
          </div>
        )}

        {searchResults === null && groupedSessions.length === 0 && archivedMap.size === 0 && (
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
