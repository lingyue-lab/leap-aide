import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import type { SessionListItem } from '@/types';
import { useSessionStore } from '@/stores/useSessionStore';
import { useChatStore } from '@/stores/useChatStore';
import { subscribe as subscribeStream } from '@/stores/streamManager';
import { getIsSessionBusy } from '@/utils/sessionBusy';
import { sessionService } from '@/services/api';
import { TrashMultiIcon, Icon } from '@/components/common/Icons';

interface SessionItemProps {
  session: SessionListItem;
  isActive: boolean;
  onClick: () => void;
  archivedChildren?: SessionListItem[];
}

export default function SessionItem({ session, isActive, onClick, archivedChildren }: SessionItemProps) {
  const { t } = useTranslation('common');
  const { renameSession, deleteSession, pinSession, updateSessionLastSeen, loadSession, selectSession, currentSessionId } = useSessionStore();

  // Unread count: how many messages were added since the user last viewed this session.
  // When last_seen_msg_count is null (never tracked), treat as fully seen.
  const sessionMsgCount = session.message_count ?? 0;
  const lastSeenMsgCount = session.last_seen_msg_count ?? sessionMsgCount;
  const unreadCount = Math.max(0, sessionMsgCount - lastSeenMsgCount);

  const [isEditing, setIsEditing] = useState(false);
  const [archivedExpanded, setArchivedExpanded] = useState(false);
  const [editTitle, setEditTitle] = useState(session.title);
  const [menuOpen, setMenuOpen] = useState(false);
  const [compressing, setCompressing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [showClearModal, setShowClearModal] = useState(false);
  const [keepLast, setKeepLast] = useState('0');
  /** True when this session has a live background stream.
   *  Initialise from active_stream_id (survives page refresh) and
   *  StreamManager (more precise for already-reconnected sessions). */
  const [bgStreaming, setBgStreaming] = useState(() =>
    !!session.active_stream_id || getIsSessionBusy(session.session_id)
  );
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });

  // [DEBUG] Log only when badge state changes meaningfully
  // Removed frequent per-render logging — use bg-stream-done and loadSessions logs instead

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

  // When the backend clears active_stream_id (stream ended), turn off the dot
  // even if we never reconnected to that stream.
  useEffect(() => {
    if (!session.active_stream_id) {
      setBgStreaming(false);
    }
  }, [session.active_stream_id]);

  // Subscribe to StreamManager to reflect background streaming state
  useEffect(() => {
    const unsub = subscribeStream((sid) => {
      if (sid === session.session_id) {
        setBgStreaming(getIsSessionBusy(session.session_id));
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
    if (bgStreaming) {
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
    window.dispatchEvent(new CustomEvent('hermes:clear-history'));
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
        className={`sidebar-item ${isActive && !isEditing ? 'active' : ''} ${bgStreaming && !isActive ? 'bg-streaming' : ''}`}
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
              <span className="sidebar-item-title">{session.title}</span>
            )}
            {/* Unread badge — shown after stream completes and user hasn't visited this session */}
            {unreadCount > 0 && !isActive && !bgStreaming && (
              <span className="sidebar-item-unread" data-tooltip={`${unreadCount} new response(s)`}>
                {unreadCount > 99 ? '99+' : `+${unreadCount}`}
              </span>
            )}
          </div>
        </div>

        <div className="sidebar-item-actions">
          {/* Streaming pulse — visible even when this session is active */}
          {bgStreaming && (
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
          <span className="sidebar-item-time">{relativeTime}</span>
          {session.is_cli_session || session._readonly ? (
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
                        <span className="sidebar-item-archived-icon" data-tooltip="Archived snapshot">
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
