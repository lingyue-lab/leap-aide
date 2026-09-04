import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import type { SessionListItem, DelegateChildSession } from '@/types';
import { useSessionStore } from '@/stores/useSessionStore';
import { useChatStore } from '@/stores/useChatStore';
import { useSessionStreaming } from '@/hooks/useSessionStreaming';
import { markSessionSeen } from '@/utils/sessionSeen';
import { sessionService } from '@/services/api';
import { TrashMultiIcon, Icon } from '@/components/common/Icons';

interface SessionItemProps {
  session: SessionListItem;
  isActive: boolean;
  onClick: () => void;
  archivedChildren?: SessionListItem[];
  delegateChildren?: DelegateChildSession[];
  onToggleDelegateChildren?: (sessionId: string) => void;
}

export default function SessionItem({ session, isActive, onClick, archivedChildren, delegateChildren, onToggleDelegateChildren }: SessionItemProps) {
  const { t } = useTranslation('common');
  const { renameSession, deleteSession, pinSession, loadSession, loadSessions, selectSession, currentSessionId } = useSessionStore();

  // Unread count: how many messages were added since the user last viewed this session.
  // When last_seen_msg_count is null (never tracked), treat as fully seen.
  const sessionMsgCount = session.message_count ?? 0;
  const lastSeenMsgCount = session.last_seen_msg_count ?? sessionMsgCount;
  const unreadCount = Math.max(0, sessionMsgCount - lastSeenMsgCount);

  const [isEditing, setIsEditing] = useState(false);
  const [archivedExpanded, setArchivedExpanded] = useState(false);
  const [delegateExpanded, setDelegateExpanded] = useState(false);
  const [editTitle, setEditTitle] = useState(session.title);
  const [menuOpen, setMenuOpen] = useState(false);
  const [compressing, setCompressing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [showClearModal, setShowClearModal] = useState(false);
  const [keepLast, setKeepLast] = useState('0');
  const [showClearDelegatesModal, setShowClearDelegatesModal] = useState(false);
  const [keepDelegates, setKeepDelegates] = useState('0');
  // Streaming pulse: use active_stream_id from sessionStore for background sessions,
  // and chatStore.activeStreamId for the active session (set immediately by StreamManager).
  const chatActiveStreamId = useChatStore((s) => s.activeStreamId);
  const isStreaming = isActive ? !!chatActiveStreamId : !!session.active_stream_id;
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });

  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Read-only marker for gateway/CLI sessions and compaction snapshots
  // Platform icon mapping for gateway sessions
  const platformIcon = (() => {
    if (!session.is_cli_session) return null;
    const tag = (session.source_tag || '').toLowerCase();
    switch (tag) {
      case 'weixin':
        return <span className="gateway-icon weixin" data-tooltip="WeChat">
          <Icon name="chat_bubble" size={16} />
        </span>;
      case 'telegram':
        return <span className="gateway-icon telegram" data-tooltip="Telegram">
          <Icon name="send" size={16} />
        </span>;
      case 'cli':
        return <span className="gateway-icon cli" data-tooltip="CLI">
          <Icon name="terminal_2" size={16} />
        </span>;
      default:
        return tag ? <span className="gateway-icon generic" data-tooltip={session.source_tag}>{tag.charAt(0).toUpperCase()}</span> : null;
    }
  })();

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  useEffect(() => {
    setEditTitle(session.title);
  }, [session.title]);

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
    if (session.is_cli_session) return;  // gateway sessions are read-only — no actions
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      setMenuPosition({
        top: rect.top + window.scrollY,
        left: rect.right + 4,
      });
    }
    setMenuOpen(true);
  };

  const handleStartEdit = () => {
    setIsEditing(true);
    setMenuOpen(false);
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
    if (isStreaming) {
      alert(t('sessions.clear_streaming_warn'));
      return;
    }
    if (!confirm(t('sessions.compress_confirm'))) return;
    setCompressing(true);
    try {
      const result = await sessionService.compressSession(session.session_id);
      // Update the chat store directly — ChatInterface only syncs
      // messages on sessionId change, but compress stays on the same session.
      if (result?.session?.messages) {
        useChatStore.getState().setMessages(result.session.messages);
        useChatStore.getState().setToolCalls(result.session.tool_calls ?? []);
        useChatStore.getState().clearCache(session.session_id);
      }
      await loadSession(session.session_id);
    } catch (err) {
      alert(`Compression failed: ${(err as Error).message}`);
    } finally {
      setCompressing(false);
    }
  };

  const handleClear = () => {
    setMenuOpen(false);
    if (isStreaming) {
      alert(t('sessions.clear_streaming_warn'));
      return;
    }
    setKeepLast('0');
    setShowClearModal(true);
  };

  const handleClearConfirm = async () => {
    setShowClearModal(false);
    setClearing(true);
    window.dispatchEvent(new CustomEvent('hermes:clear-history'));
    const keepN = Math.max(0, parseInt(keepLast, 10) || 0);
    try {
      await sessionService.clearHistory(session.session_id, keepN);
      await loadSession(session.session_id);
      // Update message_count in sessions list so the clear button becomes disabled
      useSessionStore.setState((state) => ({
        sessions: state.sessions.map((s) =>
          s.session_id === session.session_id ? { ...s, message_count: keepN } : s
        ),
      }));
      // Sync chatStore with the cleared session data
      const chatStore = useChatStore.getState();
      chatStore.clearCache(session.session_id);
      if (isActive) {
        const currentSession = useSessionStore.getState().currentSession;
        chatStore.setMessages(currentSession?.messages ?? []);
        chatStore.setToolCalls(currentSession?.tool_calls ?? []);
        chatStore.setActiveStreamId(currentSession?.active_stream_id ?? null);
        // Reset live usage so ContextUsageIndicator falls back to session
        // data (which was zeroed by the backend) instead of showing stale
        // values from before the clear.
        chatStore.setLastUsage(null);
      }
    } catch (err) {
      alert(`Clear failed: ${(err as Error).message}`);
    } finally {
      setClearing(false);
      window.dispatchEvent(new CustomEvent('hermes:clear-history-done'));
    }
  };

  const handleClearDelegatesConfirm = async () => {
    setShowClearDelegatesModal(false);
    setClearing(true);
    const keepN = Math.max(0, parseInt(keepDelegates, 10) || 0);
    try {
      const result = await sessionService.clearDelegates(session.session_id, keepN);
      await loadSessions();
      if (result.deleted > 0) {
        // Collapse delegate children view
        setDelegateExpanded(false);
      }
    } catch (err) {
      alert(t('sessions.clear_delegates_failed', { error: (err as Error).message }));
    } finally {
      setClearing(false);
    }
  };

  const handlePin = async () => {
    setMenuOpen(false);
    await pinSession(session.session_id, !session.pinned);
  };

  // Compact relative time: "5d", "3h", "12m", "just now"
  const relativeTime = (() => {
    const diffMs = Date.now() - session.created_at * 1000;
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return 'now';
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d`;
    const months = Math.floor(days / 30);
    if (months < 12) return `${months}m`;
    return `${Math.floor(months / 12)}y`;
  })();

  const handleClick = () => {
    onClick();
    markSessionSeen(session.session_id);
  };

  // Subagent icon click handler
  const handleDelegateToggle = (e: React.MouseEvent) => {
    e.stopPropagation();
    setDelegateExpanded(!delegateExpanded);
    if (!delegateChildren && onToggleDelegateChildren) {
      onToggleDelegateChildren(session.session_id);
    }
  };

  // Determine readonly state (subagent sessions are NOT readonly)
  const isSubagentSession = session.is_subagent_session;
  const isReadonly = session._readonly && !isSubagentSession;

  // Delegate children running state — reactive from notify stream
  const delegateRunningCount = session._delegate_running_count ?? 0;
  const subagentsActive = delegateRunningCount > 0;

  return (
    <>
      <div
        className={`sidebar-item ${isActive && !isEditing ? 'active' : ''} ${isStreaming && !isActive ? 'bg-streaming' : ''}`}
        onClick={isEditing ? undefined : handleClick}
        onContextMenu={handleContextMenu}
        data-session-id={session.session_id}
      >
        <div className="sidebar-item-text">
          <div className="sidebar-item-title-row">
            {isEditing ? (
              <input
                ref={inputRef}
                type="text"
                className="sidebar-item-title-input"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onBlur={handleSaveEdit}
                onKeyDown={handleKeyDown}
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <span className="sidebar-item-title">
                {isSubagentSession ? t('sessions.subagent') : session.title}
              </span>
            )}
            {/* Unread badge — shown after stream completes and user hasn't visited this session */}
            {unreadCount > 0 && !isActive && !isStreaming && (
              <span className="sidebar-item-unread" data-tooltip={`${unreadCount} new response(s)`}>
                {unreadCount > 99 ? '99+' : `+${unreadCount}`}
              </span>
            )}
          </div>
        </div>

        <div className="sidebar-item-actions">
          {/* Streaming pulse — visible even when this session is active */}
          {isStreaming && (
            <span className="sidebar-item-icon-wrap" data-tooltip={isActive ? 'Streaming' : 'Running in background'}>
              <span className="sidebar-item-streaming-dot" />
            </span>
          )}
          {session.pinned && (
            <span className="sidebar-item-pin-indicator" data-tooltip={t('sessions.pinned')}>
              <Icon name="keep" size={16} />
            </span>
          )}
          {platformIcon}
          {archivedChildren && archivedChildren.length > 0 && (
            <span
              className="sidebar-item-archived-badge"
              data-tooltip={`${archivedChildren.length} snapshot${archivedChildren.length > 1 ? 's' : ''}`}
              onClick={(e) => { e.stopPropagation(); setArchivedExpanded(!archivedExpanded); }}
            >
              <Icon name="description" size={16} />
            </span>
          )}
          {session._has_delegate_children && (
            <span
              className={`sidebar-item-delegate-badge${subagentsActive ? ' pulsing' : ''}`}
              data-tooltip={t('sessions.subagent_count', { count: session._delegate_children_count ?? 0 })}
              onClick={handleDelegateToggle}
            >
              <Icon name="account_tree" size={16} />
              {subagentsActive && (
                <span className="sidebar-item-delegate-running">
                  {delegateRunningCount}
                </span>
              )}
            </span>
          )}
          <span className="sidebar-item-time">{relativeTime}</span>
          {isReadonly ? (
            <span className="sidebar-item-actions-trigger" style={{cursor: 'default', opacity: 0.45}} title={session.is_cli_session ? t('sessions.readonly_gateway') : t('sessions.readonly_snapshot')}>
              <Icon name="lock" size={16} />
            </span>
          ) : (
            <button
              ref={buttonRef}
              className={`sidebar-item-actions-trigger ${menuOpen ? 'active' : ''}`}
              onClick={(e) => {
                e.stopPropagation();
                handleContextMenu(e);
              }}
              aria-label={t('sessions.actions')}
            >
              <Icon name="more_vert" size={16} />
            </button>
          )}
        </div>

        {menuOpen && createPortal(
          <div
            ref={menuRef}
            className="sidebar-item-action-menu open"
            style={{
              position: 'fixed',
              top: menuPosition.top,
              left: menuPosition.left,
              minWidth: 140,
            }}
          >
            {!session.is_cli_session && (
              <>
                <button className="sidebar-item-action-opt" onClick={handleStartEdit}>
                  <span className="ws-opt-action">
                    <Icon name="edit" size={16} className="ws-opt-icon" />
                    <span className="ws-opt-name">{t('sessions.rename')}</span>
                  </span>
                </button>
                <button className="sidebar-item-action-opt" onClick={handlePin}>
                  <span className="ws-opt-action">
                    <Icon name="keep" size={16} className="ws-opt-icon" />
                    <span className="ws-opt-name">
                      {session.pinned ? t('sessions.unpin') : t('sessions.pin')}
                    </span>
                  </span>
                </button>
              </>
            )}
            {!session.is_cli_session && (
              <>
                {(() => {
                  const canCompress = (session.message_count ?? 0) > 50;
                  return (
                    <button
                      className={`sidebar-item-action-opt${!canCompress ? ' disabled' : ''}`}
                      onClick={() => { if (canCompress) handleCompress(); }}
                      {...(!canCompress ? { 'data-tooltip': t('sessions.compress_min_count') } : {})}
                    >
                      <span className="ws-opt-action">
                        {compressing ? (
                          <Icon name="progress_activity" size={16} className="ws-opt-icon ws-opt-icon--spin" />
                        ) : (
                          <Icon name="compress" size={16} className="ws-opt-icon" />
                        )}
                        <span className="ws-opt-name">
                          {compressing ? t('sessions.compressing') : t('sessions.compress_history')}
                        </span>
                      </span>
                    </button>
                  );
                })()}
                {(() => {
                  const canClear = (session.message_count ?? 0) > 0;
                  return (
                    <button
                      className={`sidebar-item-action-opt${!canClear ? ' disabled' : ''}`}
                      onClick={() => { if (canClear) handleClear(); }}
                      {...(!canClear ? { 'data-tooltip': t('sessions.clear_empty') } : {})}
                    >
                      <span className="ws-opt-action">
                        {clearing ? (
                          <Icon name="progress_activity" size={16} className="ws-opt-icon ws-opt-icon--spin" />
                        ) : (
                          <Icon name="delete" size={16} className="ws-opt-icon" />
                        )}
                        <span className="ws-opt-name">
                          {clearing ? t('sessions.clearing') : t('sessions.clear_history')}
                        </span>
                      </span>
                    </button>
                  );
                })()}
                {(() => {
                  const canClearDelegates = !!session._has_delegate_children;
                  return (
                    <button
                      className={`sidebar-item-action-opt${!canClearDelegates ? ' disabled' : ''}`}
                      onClick={() => {
                        if (canClearDelegates) {
                          setMenuOpen(false);
                          setKeepDelegates('0');
                          setShowClearDelegatesModal(true);
                        }
                      }}
                      {...(!canClearDelegates ? { 'data-tooltip': t('sessions.clear_delegates_empty') } : {})}
                    >
                      <span className="ws-opt-action">
                        <Icon name="account_tree" size={16} className="ws-opt-icon" />
                        <span className="ws-opt-name">{t('sessions.clear_delegates')}</span>
                      </span>
                    </button>
                  );
                })()}
              </>
            )}
            {!session.is_cli_session && (
              <button
                className={`sidebar-item-action-opt danger${session.pinned ? ' disabled' : ''}`}
                onClick={() => { if (!session.pinned) handleDelete(); }}
                {...(session.pinned ? { 'data-tooltip': t('sessions.unpin_to_delete') } : {})}
              >
                <span className="ws-opt-action">
                    <TrashMultiIcon size={16} className="ws-opt-icon" />
                  <span className="ws-opt-name">{t('sessions.delete')}</span>
                </span>
              </button>
            )}
          </div>,
          document.body
        )}
      </div>

      {/* Archived (compaction) child sessions — collapsible, read-only */}
      {archivedChildren && archivedChildren.length > 0 && (
        <div className="sidebar-item-archived-section">
          {archivedExpanded && (
            <div className="sidebar-item-archived-children">
              {(() => {
                const sorted = [...archivedChildren].sort((a, b) => (b._compaction_n || 0) - (a._compaction_n || 0));
                const total = sorted.length;
                return sorted.map((child, i) => {
                  const ordinal = total - i; // 1 = oldest, total = most recent
                  const label = ordinal === total ? t('sessions.latest_snapshot', 'Latest')
                    : t('sessions.prev_snapshot', { defaultValue: `Recent {{n}}`, n: total - ordinal });
                  return (
                  <div
                    key={child.session_id}
                    className={`sidebar-item sidebar-item-archived ${currentSessionId === child.session_id ? 'active' : ''}`}
                    onClick={(e) => { e.stopPropagation(); selectSession(child.session_id); }}
                    data-session-id={child.session_id}
                  >
                    <div className="sidebar-item-text">
                      <div className="sidebar-item-title-row">
                        <span className="sidebar-item-child-icon" data-tooltip="Archived snapshot">
                          <Icon name="description" size={16} />
                        </span>
                        <span className="sidebar-item-title">
                          {label} · {child.message_count ?? 0} msgs
                        </span>
                      </div>
                    </div>
                  </div>
                );
                });
              })()}
            </div>
          )}
        </div>
      )}

      {/* Delegate (subagent) child sessions — collapsible, readable */}
      {delegateChildren && delegateChildren.length > 0 && (
        <div className="sidebar-item-archived-section">
          {delegateExpanded && (
            <div className="sidebar-item-archived-children">
              {delegateChildren.map((child) => (
                <DelegateChildItem
                  key={child.id}
                  child={child}
                  isActive={currentSessionId === child.id}
                  onClick={() => selectSession(child.id)}
                />
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

      {/* Clear Delegates Modal */}
      {showClearDelegatesModal && (
        <div className="clear-history-overlay" onClick={() => setShowClearDelegatesModal(false)}>
          <div className="clear-history-modal" onClick={(e) => e.stopPropagation()}>
            <div className="clear-history-modal__title">{t('sessions.clear_delegates_confirm')}</div>
            <div className="clear-history-modal__desc">{t('sessions.clear_delegates_desc')}</div>
            <div className="clear-history-modal__field">
              <label className="clear-history-modal__label">{t('sessions.clear_delegates_keep_label')}</label>
              <input
                className="clear-history-modal__input"
                type="number"
                min="0"
                value={keepDelegates}
                onChange={(e) => setKeepDelegates(e.target.value)}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleClearDelegatesConfirm();
                  if (e.key === 'Escape') setShowClearDelegatesModal(false);
                }}
              />
            </div>
            <div className="clear-history-modal__actions">
              <button className="clear-history-modal__btn clear-history-modal__btn--cancel" onClick={() => setShowClearDelegatesModal(false)}>
                {t('common.cancel')}
              </button>
              <button className="clear-history-modal__btn clear-history-modal__btn--confirm" onClick={handleClearDelegatesConfirm}>
                {t('common.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/**
 * DelegateChildItem — A single delegate (subagent) child session in the sidebar.
 * Uses useSessionStreaming hook to subscribe to streaming state changes
 * for real-time breathing animation.
 */
function DelegateChildItem({ child, isActive, onClick }: {
  child: DelegateChildSession;
  isActive: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation('common');
  const childStreaming = useSessionStreaming(child.id);

  return (
    <div
      className={`sidebar-item sidebar-item-archived ${isActive ? 'active' : ''}`}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      data-session-id={child.id}
    >
      <div className="sidebar-item-text">
        <div className="sidebar-item-title-row">
          <span className={`sidebar-item-child-icon${childStreaming ? ' streaming' : ''}`} data-tooltip={t('sessions.subagent_session')}>
            <Icon name="account_tree" size={16} />
          </span>
          <span className="sidebar-item-title">
            {t('sessions.subagent')}
          </span>
        </div>
      </div>
    </div>
  );
}
