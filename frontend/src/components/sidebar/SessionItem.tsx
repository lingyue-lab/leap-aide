import React, { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { formatDistanceToNow } from 'date-fns';
import { zhCN, enUS } from 'date-fns/locale';
import type { SessionListItem, Personality } from '@/types';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useChatStore } from '@/stores/useChatStore';
import { isSessionStreaming, subscribe as subscribeStream } from '@/stores/streamManager';
import { sessionService, personalityService } from '@/services/api';

interface SessionItemProps {
  session: SessionListItem;
  isActive: boolean;
  onClick: () => void;
  archivedChildren?: SessionListItem[];
}

export default function SessionItem({ session, isActive, onClick, archivedChildren }: SessionItemProps) {
  const { t, i18n } = useTranslation('common');
  const { renameSession, deleteSession, pinSession, assignWorkspace, updateSessionLastSeen, updateSessionPersonality, loadSession, selectSession, currentSessionId } = useSessionStore();
  const { workspaces } = useWorkspaceStore();

  // Unread count: how many messages were added since the user last viewed this session
  const sessionMsgCount = session.message_count ?? 0;
  const lastSeenMsgCount = session.last_seen_msg_count ?? 0;
  const unreadCount = Math.max(0, sessionMsgCount - lastSeenMsgCount);

  const [isEditing, setIsEditing] = useState(false);
  const [archivedExpanded, setArchivedExpanded] = useState(false);
  const [editTitle, setEditTitle] = useState(session.title);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showWorkspaceMenu, setShowWorkspaceMenu] = useState(false);
  const [showPersonalityMenu, setShowPersonalityMenu] = useState(false);
  const [personalities, setPersonalities] = useState<Personality[]>([]);
  const [compressing, setCompressing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [showClearModal, setShowClearModal] = useState(false);
  const [keepLast, setKeepLast] = useState('0');
  /** True when this session has a live background stream */
  const [bgStreaming, setBgStreaming] = useState(() => isSessionStreaming(session.session_id));
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });

  // [DEBUG] Log only when badge state changes meaningfully
  // Removed frequent per-render logging — use bg-stream-done and loadSessions logs instead

  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const dateLocale = i18n.language === 'zh-CN' ? zhCN : enUS;

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  useEffect(() => {
    setEditTitle(session.title);
  }, [session.title]);

  // Fetch available personalities on mount
  useEffect(() => {
    personalityService.getAll().then(setPersonalities).catch(() => {});
  }, []);

  // Subscribe to StreamManager to reflect background streaming state
  useEffect(() => {
    const unsub = subscribeStream((sid) => {
      if (sid === session.session_id) {
        setBgStreaming(isSessionStreaming(session.session_id));
      }
    });
    return unsub;
  }, [session.session_id]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    if (menuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [menuOpen]);

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      setMenuPosition({
        top: rect.bottom + window.scrollY,
        left: rect.left + window.scrollX,
      });
    }
    setShowWorkspaceMenu(false);
    setShowPersonalityMenu(false);
    setMenuOpen(true);
  };

  const handleStartEdit = () => {
    setIsEditing(true);
    setMenuOpen(false);
  };

  const handleAssignWorkspace = () => {
    setShowWorkspaceMenu((prev) => !prev);
  };

  const handlePersonalityMenu = () => {
    setShowPersonalityMenu((prev) => !prev);
  };

  const handleSelectPersonality = async (name: string | null) => {
    setMenuOpen(false);
    setShowPersonalityMenu(false);
    try {
      await updateSessionPersonality(session.session_id, name);
    } catch (err) {
      console.error('Failed to set personality:', err);
    }
  };

  const handleSelectWorkspace = async (workspacePath: string) => {
    try {
      await assignWorkspace(session.session_id, workspacePath);
    } catch (err) {
      console.error('Failed to assign workspace:', err);
    }
  };

  const handleSaveEdit = async () => {
    if (editTitle.trim() && editTitle !== session.title) {
      await renameSession(session.session_id, editTitle.trim());
    }
    setIsEditing(false);
  };

  const handleCancelEdit = () => {
    setEditTitle(session.title);
    setIsEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSaveEdit();
    } else if (e.key === 'Escape') {
      handleCancelEdit();
    }
  };

  const handleDelete = async () => {
    setMenuOpen(false);
    if (confirm(t('sessions.confirm_delete'))) {
      await deleteSession(session.session_id);
    }
  };

  const handleCompress = async () => {
    setMenuOpen(false);
    if (bgStreaming) {
      alert(t('sessions.clear_streaming_warn'));
      return;
    }
    if (!confirm(t('sessions.compress_confirm'))) return;
    setCompressing(true);
    try {
      await sessionService.compressSession(session.session_id);
      // Reload the session so the chat view shows the compressed messages
      await loadSession(session.session_id);
    } catch (err) {
      alert(`Compression failed: ${(err as Error).message}`);
    } finally {
      setCompressing(false);
    }
  };

  const handleClear = () => {
    setMenuOpen(false);
    if (bgStreaming) {
      alert(t('sessions.clear_streaming_warn'));
      return;
    }
    setKeepLast('0');
    setShowClearModal(true);
  };

  const handleClearConfirm = async () => {
    setShowClearModal(false);
    setClearing(true);
    const keepN = Math.max(0, parseInt(keepLast, 10) || 0);
    try {
      await sessionService.clearHistory(session.session_id, keepN);
      await loadSession(session.session_id);
      // Sync chatStore with the cleared session data
      const chatStore = useChatStore.getState();
      chatStore.clearCache(session.session_id);
      if (isActive) {
        const currentSession = useSessionStore.getState().currentSession;
        chatStore.setMessages(currentSession?.messages ?? []);
        chatStore.setToolCalls(currentSession?.tool_calls ?? []);
        chatStore.setActiveStreamId(currentSession?.active_stream_id ?? null);
      }
    } catch (err) {
      alert(`Clear failed: ${(err as Error).message}`);
    } finally {
      setClearing(false);
    }
  };

  const handlePin = async () => {
    setMenuOpen(false);
    setShowWorkspaceMenu(false);
    await pinSession(session.session_id, !session.pinned);
  };

  const relativeTime = formatDistanceToNow(new Date(session.created_at * 1000), {
    addSuffix: false,
    locale: dateLocale,
  });

  const handleClick = () => {
    onClick();
    // Mark as seen: persist current message count so badge clears
    if (unreadCount > 0) {
      const currentCount = session.message_count ?? 0;
      // Optimistic update immediately (no flicker wait)
      updateSessionLastSeen(session.session_id, currentCount);
      // Persist to backend (fire-and-forget)
      sessionService.markSessionSeen(session.session_id, currentCount).catch(() => {
        // Ignore errors — worst case badge reappears on next page load
      });
    }
  };

  return (
    <>
      <div
        className={`session-item ${isActive ? 'active' : ''} ${bgStreaming && !isActive ? 'bg-streaming' : ''}`}
        onClick={isEditing ? undefined : handleClick}
        onContextMenu={handleContextMenu}
        data-session-id={session.session_id}
      >
        <div className="session-text">
          <div className="session-title-row">
            {session.pinned && (
              <span className="session-pin-indicator" title={t('sessions.pinned')}>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
                  <path d="M16 1L23 8L17.5 13.5L17.5 16L13.5 16L12 17.5L12 20L10 20L10 17.5L8.5 16L6.5 16L6.5 13.5C4.5 13.5 2 11 1 8L8.5 8L16 1Z" />
                  <line x1="8" y1="16" x2="2" y2="22" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </span>
            )}
            {isEditing ? (
              <input
                ref={inputRef}
                type="text"
                className="session-title-input"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onBlur={handleSaveEdit}
                onKeyDown={handleKeyDown}
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <span className="session-title">{session.title}</span>
            )}
            {/* Background streaming indicator — shown only when NOT the active session */}
            {bgStreaming && !isActive && (
              <span className="session-bg-streaming-dot" title="Running in background" />
            )}
            {/* Unread badge — shown after stream completes and user hasn't visited this session */}
            {unreadCount > 0 && !isActive && !bgStreaming && (
              <span className="session-unread-badge" title={`${unreadCount} new response(s)`}>
                +{unreadCount}
              </span>
            )}
          </div>
          <div className="session-meta">{relativeTime}</div>
        </div>

        <div className="session-actions">
          <button
            ref={buttonRef}
            className={`session-actions-trigger ${menuOpen ? 'active' : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              handleContextMenu(e);
            }}
            aria-label={t('sessions.actions')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="1" />
              <circle cx="12" cy="5" r="1" />
              <circle cx="12" cy="19" r="1" />
            </svg>
          </button>
        </div>

        {menuOpen && (
          <div
            ref={menuRef}
            className="session-action-menu open"
            style={{
              position: 'fixed',
              top: menuPosition.top,
              left: menuPosition.left,
              minWidth: (showWorkspaceMenu || showPersonalityMenu) ? 220 : 140,
            }}
          >
            <button className="session-action-opt" onClick={handleStartEdit}>
              <span className="ws-opt-action">
                <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                </svg>
                <span className="ws-opt-name">{t('sessions.rename')}</span>
              </span>
            </button>
            <button className="session-action-opt" onClick={handlePin}>
              <span className="ws-opt-action">
                <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 17v-7m-4 4l4-4 4 4" />
                </svg>
                <span className="ws-opt-name">
                  {session.pinned ? t('sessions.unpin') : t('sessions.pin')}
                </span>
              </span>
            </button>
            <button className="session-action-opt" onClick={handleAssignWorkspace}>
              <span className="ws-opt-action">
                <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                </svg>
                <span className="ws-opt-name">{t('sessions.assign_workspace')}</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginLeft: 'auto', opacity: 0.5 }}>
                  <polyline points={showWorkspaceMenu ? '18 15 12 9 6 15' : '9 18 15 12 9 6'}/>
                </svg>
              </span>
            </button>
            {showWorkspaceMenu && (
              <div className="session-workspace-submenu">
                {workspaces.length === 0 ? (
                  <div className="session-workspace-empty">{t('workspaces.empty')}</div>
                ) : (
                  workspaces.map((ws) => (
                    <button
                      key={ws.path}
                      className="session-action-opt"
                      onClick={() => handleSelectWorkspace(ws.path)}
                    >
                      <span className="ws-opt-action">
                        <svg className="ws-opt-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                        </svg>
                        <span className="ws-opt-name ws-opt-name--ellipsis">
                          {ws.name}
                        </span>
                        {session.workspace === ws.path && (
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginLeft: 'auto', flexShrink: 0, opacity: 0.6 }}>
                            <polyline points="20 6 9 17 4 12"/>
                          </svg>
                        )}
                      </span>
                    </button>
                  ))
                )}
              </div>
            )}
            <button className="session-action-opt" onClick={handlePersonalityMenu}>
              <span className="ws-opt-action">
                <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="10"/>
                  <path d="M12 6v6l4 2"/>
                </svg>
                <span className="ws-opt-name">{t('sessions.personality')}</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginLeft: 'auto', opacity: 0.5 }}>
                  <polyline points={showPersonalityMenu ? '18 15 12 9 6 15' : '9 18 15 12 9 6'}/>
                </svg>
              </span>
            </button>
            {showPersonalityMenu && (
              <div className="session-workspace-submenu">
                <button
                  className="session-action-opt"
                  onClick={() => handleSelectPersonality(null)}
                >
                  <span className="ws-opt-action">
                    <svg className="ws-opt-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10"/>
                      <line x1="15" y1="9" x2="9" y2="15"/>
                      <line x1="9" y1="9" x2="15" y2="15"/>
                    </svg>
                    <span className="ws-opt-name ws-opt-name--ellipsis">
                      {t('sessions.no_personality')}
                    </span>
                    {!session.personality && (
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginLeft: 'auto', flexShrink: 0, opacity: 0.6 }}>
                        <polyline points="20 6 9 17 4 12"/>
                      </svg>
                    )}
                  </span>
                </button>
                {personalities.map((p) => (
                  <button
                    key={p.name}
                    className="session-action-opt"
                    onClick={() => handleSelectPersonality(p.name)}
                  >
                    <span className="ws-opt-action">
                      <svg className="ws-opt-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                        <circle cx="12" cy="7" r="4"/>
                      </svg>
                      <span className="ws-opt-name ws-opt-name--ellipsis">
                        {p.name}
                      </span>
                      {session.personality === p.name && (
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginLeft: 'auto', flexShrink: 0, opacity: 0.6 }}>
                          <polyline points="20 6 9 17 4 12"/>
                        </svg>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            )}
            {(session.message_count ?? 0) > 50 && (
              <button
                className="session-action-opt"
                onClick={handleCompress}
                disabled={compressing || clearing}
              >
                <span className="ws-opt-action">
                  {compressing ? (
                    <svg className="ws-opt-icon ws-opt-icon--spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                    </svg>
                  ) : (
                    <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M6 9l6 6 6-6"/>
                      <path d="M6 15l6 6 6-6" opacity="0.5"/>
                    </svg>
                  )}
                  <span className="ws-opt-name">
                    {compressing ? t('sessions.compressing') : t('sessions.compress_history')}
                  </span>
                </span>
              </button>
            )}
            {(session.message_count ?? 0) > 0 && (
              <button
                className="session-action-opt"
                onClick={handleClear}
                disabled={clearing || compressing}
              >
                <span className="ws-opt-action">
                  {clearing ? (
                    <svg className="ws-opt-icon ws-opt-icon--spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
                    </svg>
                  ) : (
                    <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M3 6h18"/>
                      <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>
                      <path d="M19 6l-1 14c0 1-1 2-2 2H8c-1 0-2-1-2-2L5 6"/>
                      <line x1="10" y1="11" x2="10" y2="17"/>
                      <line x1="14" y1="11" x2="14" y2="17"/>
                    </svg>
                  )}
                  <span className="ws-opt-name">
                    {clearing ? t('sessions.clearing') : t('sessions.clear_history')}
                  </span>
                </span>
              </button>
            )}
            <button className="session-action-opt danger" onClick={handleDelete}>
              <span className="ws-opt-action">
                <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
                </svg>
                <span className="ws-opt-name">{t('sessions.delete')}</span>
              </span>
            </button>
          </div>
        )}
      </div>

      {/* Archived (compaction) child sessions — collapsible, read-only */}
      {archivedChildren && archivedChildren.length > 0 && (
        <div className="session-archived-section">
          <div
            className="session-archived-toggle"
            onClick={(e) => {
              e.stopPropagation();
              setArchivedExpanded(!archivedExpanded);
            }}
          >
            <span className={`session-archived-caret ${archivedExpanded ? 'expanded' : ''}`}>▸</span>
            <span className="session-archived-count">
              {archivedChildren.length} snapshot{archivedChildren.length > 1 ? 's' : ''}
            </span>
          </div>
          {archivedExpanded && (
            <div className="session-archived-children">
              {[...archivedChildren]
                .sort((a, b) => (b._compaction_n || 0) - (a._compaction_n || 0))
                .map((child) => (
                  <div
                    key={child.session_id}
                    className={`session-item session-item-archived ${currentSessionId === child.session_id ? 'active' : ''}`}
                    onClick={(e) => { e.stopPropagation(); selectSession(child.session_id); }}
                    data-session-id={child.session_id}
                  >
                    <div className="session-text">
                      <div className="session-title-row">
                        <span className="session-archived-icon">📜</span>
                        <span className="session-title">
                          #{child._compaction_n} · {child.message_count ?? 0} msgs
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      {/* Clear History Modal */}
      {showClearModal && (
        <div className="clear-history-overlay" onClick={() => setShowClearModal(false)}>
          <div className="clear-history-modal" onClick={(e) => e.stopPropagation()}>
            <div className="clear-history-modal__title">{t('sessions.clear_history')}</div>
            <div className="clear-history-modal__desc">{t('sessions.clear_keep_desc')}</div>
            <div className="clear-history-modal__field">
              <label className="clear-history-modal__label">{t('sessions.clear_keep_label')}</label>
              <input
                className="clear-history-modal__input"
                type="number"
                min="0"
                value={keepLast}
                onChange={(e) => setKeepLast(e.target.value)}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleClearConfirm();
                  if (e.key === 'Escape') setShowClearModal(false);
                }}
              />
            </div>
            <div className="clear-history-modal__actions">
              <button className="clear-history-modal__btn clear-history-modal__btn--cancel" onClick={() => setShowClearModal(false)}>
                {t('common.cancel')}
              </button>
              <button className="clear-history-modal__btn clear-history-modal__btn--confirm" onClick={handleClearConfirm}>
                {t('common.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
