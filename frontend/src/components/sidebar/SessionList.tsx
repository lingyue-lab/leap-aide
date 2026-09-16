import { useEffect, useState, useMemo, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { resolveWorkspaceDisplay } from '@/hooks/useWorkspaceDisplay';
import SessionItem from './SessionItem';
import CustomScrollbar from '@/components/common/CustomScrollbar';
import SessionSearchModal from '@/components/common/SessionSearchModal';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { Icon } from '@/components/common/Icons';
import type { DelegateChildSession } from '@/types';


export default function SessionList() {
  const { t } = useTranslation('common');
  const { sessions, currentSessionId, currentSession, loadSessions, createSession, selectSession } = useSessionStore();
  const delegateChildrenVersion = useSessionStore((s) => s.delegateChildrenVersion);
  const { workspaces, workspacesLoaded } = useWorkspaceStore();
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
  const [showSearchModal, setShowSearchModal] = useState(false);

  const showCliSessions = useSettingsStore((s) => s.settings?.show_cli_sessions ?? true);
  const showMessagingSessions = useSettingsStore((s) => s.settings?.show_messaging_sessions ?? true);
  const showCronSessions = useSettingsStore((s) => s.settings?.show_cron_sessions ?? false);

  // Load sessions on mount, and re-load whenever any source toggle changes so
  // the sidebar immediately reflects the new visibility.
  useEffect(() => {
    loadSessions();
  }, [loadSessions, showCliSessions, showMessagingSessions, showCronSessions]);

  // Client-side source filter — mirrors backend session_visible(). This is a
  // final display gate so SSE-pushed sessions also respect the three toggles.
  const isVisibleBySource = useCallback((s: any) => {
    if (!s.is_cli_session) return true;               // WebUI session — always visible
    const tag = s.source_tag;
    if (tag === 'cli') return showCliSessions;
    if (tag === 'cron') return showCronSessions;
    return showMessagingSessions;                     // messaging platforms + any future gateway
  }, [showCliSessions, showMessagingSessions, showCronSessions]);

  // Filter sessions: server-side search when searchResults is set, else client-side fallback
  const filteredSessions = useMemo(() => {
    // Use server search results if available (already server-filtered by source)
    if (searchResults !== null) return searchResults;
    const sessionList = (sessions || []).filter(isVisibleBySource);
    // Client-side fallback (no search active)
    if (!searchQuery.trim()) return sessionList;
    const query = searchQuery.toLowerCase();
    return sessionList.filter((s) => s.title.toLowerCase().includes(query));
  }, [sessions, searchQuery, searchResults, isVisibleBySource]);

  // Resolve a workspace path to a display label
  const wsLabel = useMemo(() => {
    const noWsLabel = t('sessions.no_workspace') || 'No Workspace';
    return (path: string | undefined): string => {
      return resolveWorkspaceDisplay(path, workspaces, noWsLabel).label;
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

  // Delegate children map (lazy-loaded when expanded)
  const [delegateChildrenMap, setDelegateChildrenMap] = useState<Map<string, DelegateChildSession[]>>(new Map());

  const loadDelegateChildren = useCallback(async (sessionId: string) => {
    if (delegateChildrenMap.has(sessionId)) return;
    try {
      const res = await fetch(`/api/sessions/${sessionId}/children`);
      const data = await res.json();
      setDelegateChildrenMap(prev => {
        const next = new Map(prev);
        next.set(sessionId, data.children || []);
        return next;
      });
    } catch {
      // non-fatal
    }
  }, [delegateChildrenMap]);

  // Re-fetch children for every currently-expanded parent when the backend
  // reports a delegate-children change (child added, completed, or deleted).
  // Without this, stale child IDs — e.g. from a delegation that was dropped
  // and rolled back out of state.db — stay cached forever and 404 on click.
  const refreshDelegateChildren = useCallback(async (parentIds: string[]) => {
    await Promise.all(parentIds.map(async (pid) => {
      try {
        const res = await fetch(`/api/sessions/${pid}/children`);
        if (!res.ok) return;
        const data = await res.json();
        setDelegateChildrenMap(prev => {
          const next = new Map(prev);
          next.set(pid, data.children || []);
          return next;
        });
      } catch {
        // non-fatal
      }
    }));
  }, []);

  useEffect(() => {
    if (delegateChildrenVersion === 0) return;
    const parentIds = Array.from(delegateChildrenMap.keys());
    if (parentIds.length > 0) {
      refreshDelegateChildren(parentIds);
    }
    // Intentionally omit delegateChildrenMap — it's read fresh each time the
    // version bumps; including it would re-run this effect on every fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [delegateChildrenVersion]);



  // All sessions (WebUI + gateway/CLI), excluding archived compaction snapshots
  // and subagent sessions.  Deduplicate by session_id — race conditions between
  // WebSocket upsert_session and HTTP createSession can produce two entries for
  // the same session; keep the first (most complete) occurrence.
  const allSessions = useMemo(() => {
    const seen = new Set<string>();
    return filteredSessions.filter((s) => {
      // Subagent exclusion: prefer the explicit flag, fall back to
      // `_parent_session_id` for older backend payloads that don't populate
      // `is_subagent_session` in /api/sessions compact dict.
      // === true (not truthy) so an explicit `false` wins over the fallback;
      // == null tolerates both undefined and null from older payloads.
      const isSubagent = s.is_subagent_session === true
        || (s.is_subagent_session == null && s._parent_session_id != null);
      if ((s._readonly && s._parent_session_id) || isSubagent) return false;
      if (seen.has(s.session_id)) return false;
      seen.add(s.session_id);
      return true;
    });
  }, [filteredSessions]);

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
    // no-op: user already in welcome state (welcome is a single-way door —
    // clicking + again does nothing; they must type or refresh).
    if (useSessionStore.getState().pendingNewSession) return;
    // Inherit workspace from the current active session; fallback to Home (undefined → backend default)
    const activeWs = sessions.find((s) => s.session_id === currentSessionId)?.workspace
      || currentSession?.workspace;
    await createSession(activeWs || undefined);
  };

  const runSearch = useCallback(async (query: string) => {
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
  }, [sessions]);

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
    debounceRef.current = setTimeout(() => {
      runSearch(query);
    }, DEBOUNCE_MS);
  }, [runSearch]);

  // Re-run the active server search when source toggles change, so search
  // results stay in sync with what the sidebar shows.
  useEffect(() => {
    if (searchQuery.trim() && searchResults !== null) {
      setSearching(true);
      runSearch(searchQuery);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCliSessions, showMessagingSessions, showCronSessions]);

  const handleOpenSearch = useCallback(() => {
    setShowSearchModal(true);
  }, []);

  const handleCloseSearch = useCallback(() => {
    setShowSearchModal(false);
    setSearchQuery('');
    setSearchResults(null);
  }, []);

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

  if (!workspacesLoaded) {
    return <div className="flex-1 flex items-center justify-center"><LoadingSpinner mode="inline" minimal /></div>;
  }

  return (
    <>
      {/* ── Search Modal (DeepSeek-style centred command palette) ── */}
      <SessionSearchModal
        open={showSearchModal}
        onClose={handleCloseSearch}
        placeholder={t('sessions.filter')}
        value={searchQuery}
        onChange={handleSearchChange}
      >
        {searchQuery && !searching && searchResults !== null && (
          <div className="session-search-info">
            {`${searchResults.length} result${searchResults.length !== 1 ? 's' : ''}`}
          </div>
        )}
        {searchQuery && !searching && searchResults !== null && searchResults.length > 0 && (
          <div className="session-search-results">
            {searchResults.map((session) => (
              <SessionItem
                key={session.session_id}
                session={session}
                isActive={currentSessionId === session.session_id}
                onClick={() => { handleCloseSearch(); selectSession(session.session_id, (session as any).match_message_id ?? null); }}
                archivedChildren={archivedMap.get(session.session_id)}
              />
            ))}
          </div>
        )}
        {searchQuery && !searching && searchResults?.length === 0 && (
          <div className="session-search-empty">No matching conversations</div>
        )}
      </SessionSearchModal>

      <div className="sidebar-header">
        <span className="sidebar-header-title">{t('tabs.chat')}</span>
        <div className="sidebar-header-actions">
          <button
            className="icon-btn"
            onClick={handleOpenSearch}
            title={t('sessions.filter')}
          >
            <Icon name="search" size={18} />
          </button>
          {(showCliSessions || showMessagingSessions || showCronSessions) && (
            <button
              className="icon-btn"
              onClick={() => handleCleanupOpen()}
              title="Clean up old gateway sessions"
            >
              <Icon name="delete" size={18} />
            </button>
          )}
          <button
            className="icon-btn"
            onClick={handleNewChat}
            title={t('sessions.new_conversation') + ' (Cmd+K)'}
          >
            <Icon name="add" size={18} />
          </button>
        </div>
      </div>

      <CustomScrollbar className="session-list">
        {/* Normal grouped view (only when not searching) */}
        {searchResults === null && groupedSessions.length > 0 && (
          <div className="webui-sessions-section">
            {groupedSessions.map((group) => {
              const collapsed = collapsedKeys.has(group.key);
              return (
                <div key={group.key}>
                  <div
                    className={`sidebar-items-group-header${collapsed ? ' collapsed' : ''}`}
                    onClick={() => toggleGroup(group.key)}
                  >
                    <Icon name="keyboard_arrow_down" size={14} className="sidebar-items-group-caret" />
                    <span className="sidebar-items-group-label">{group.label}</span>
                    <span className="sidebar-items-group-count">{group.sessions.length}</span>
                  </div>
                  {!collapsed && group.sessions.map((session) => (
                    <SessionItem
                      key={session.session_id}
                      session={session}
                      isActive={currentSessionId === session.session_id}
                      onClick={() => selectSession(session.session_id, (session as any).match_message_id ?? null)}
                      archivedChildren={archivedMap.get(session.session_id)}
                      delegateChildren={delegateChildrenMap.get(session.session_id)}
                      onToggleDelegateChildren={loadDelegateChildren}
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
      </CustomScrollbar>

      {/* Cleanup Modal */}
      {showCleanupModal && (
        <div className="clear-history-overlay" onClick={handleCleanupClose}>
          <div className="clear-history-modal" onClick={(e) => e.stopPropagation()}>
            <div className="clear-history-modal__title">{t('sessions.cleanup_title')}</div>
            <div className="clear-history-modal__desc">
              {t('sessions.cleanup_desc')}
            </div>
            <div className="clear-history-modal__field">
              <label className="clear-history-modal__label">{t('sessions.cleanup_source')}</label>
              <select
                className="clear-history-modal__input"
                value={cleanupSource}
                onChange={(e) => setCleanupSource(e.target.value)}
              >
                <option value="">{t('sessions.cleanup_source_placeholder')}</option>
                <option value="all">{t('sessions.cleanup_source_all')}</option>
                <option value="webui">{t('sessions.cleanup_source_webui')}</option>
                <option value="cron">{t('sessions.cleanup_source_cron')}</option>
                <option value="cli">{t('sessions.cleanup_source_cli')}</option>
                <option value="message">{t('sessions.cleanup_source_message')}</option>
              </select>
            </div>
            <div className="clear-history-modal__field">
              <label className="clear-history-modal__label">{t('sessions.cleanup_older_than')}</label>
              <select
                className="clear-history-modal__input"
                value={cleanupDays}
                onChange={(e) => setCleanupDays(Number(e.target.value))}
              >
                <option value={7}>{t('sessions.cleanup_days', { count: 7 })}</option>
                <option value={30}>{t('sessions.cleanup_days', { count: 30 })}</option>
                <option value={90}>{t('sessions.cleanup_days', { count: 90 })}</option>
              </select>
            </div>
            {cleanupPreview !== null && (
              <div className="text-sm text-green-500 mb-2">{t('sessions.cleanup_deleted', { count: cleanupPreview })}</div>
            )}
            <div className="clear-history-modal__actions">
              <button className="clear-history-modal__btn clear-history-modal__btn--cancel" onClick={handleCleanupClose}>
                {t('cancel')}
              </button>
              <button
                className="clear-history-modal__btn clear-history-modal__btn--confirm"
                onClick={handleCleanupConfirm}
                disabled={!cleanupSource || cleanupRunning}
              >
                {cleanupRunning ? t('sessions.cleanup_cleaning') : t('sessions.cleanup_clean')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
