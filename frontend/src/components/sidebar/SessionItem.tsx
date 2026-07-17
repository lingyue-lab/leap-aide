import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import type { SessionListItem } from '@/types';
import { useSessionStore } from '@/stores/useSessionStore';
import { useChatStore } from '@/stores/useChatStore';
import { isSessionStreaming, subscribe as subscribeStream } from '@/stores/streamManager';
import { sessionService } from '@/services/api';

interface SessionItemProps {
  session: SessionListItem;
  isActive: boolean;
  onClick: () => void;
  archivedChildren?: SessionListItem[];
}

export default function SessionItem({ session, isActive, onClick, archivedChildren }: SessionItemProps) {
  const { t } = useTranslation('common');
  const { renameSession, deleteSession, pinSession, updateSessionLastSeen, loadSession, selectSession, currentSessionId } = useSessionStore();

  // Unread count: how many messages were added since the user last viewed this session
  const sessionMsgCount = session.message_count ?? 0;
  const lastSeenMsgCount = session.last_seen_msg_count ?? 0;
  const unreadCount = Math.max(0, sessionMsgCount - lastSeenMsgCount);

  const [isEditing, setIsEditing] = useState(false);
  const [archivedExpanded, setArchivedExpanded] = useState(false);
  const [editTitle, setEditTitle] = useState(session.title);
  const [menuOpen, setMenuOpen] = useState(false);
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

  // Read-only marker for gateway/CLI sessions and compaction snapshots
  // Platform icon mapping for gateway sessions
  const platformIcon = (() => {
    if (!session.is_cli_session) return null;
    const tag = (session.source_tag || '').toLowerCase();
    switch (tag) {
      case 'weixin':
        return <span className="gateway-icon weixin" data-tooltip="WeChat">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </span>;
      case 'telegram':
        return <span className="gateway-icon telegram" data-tooltip="Telegram">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="22" y1="2" x2="11" y2="13"/>
            <polygon points="22 2 15 22 11 13 2 9 22 2"/>
          </svg>
        </span>;
      case 'cli':
        return <span className="gateway-icon cli" data-tooltip="CLI">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="4 17 10 11 4 5"/>
            <line x1="12" y1="19" x2="20" y2="19"/>
          </svg>
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
        className={`session-item ${isActive ? 'active' : ''} ${bgStreaming && !isActive ? 'bg-streaming' : ''}`}
        onClick={isEditing ? undefined : handleClick}
        onContextMenu={handleContextMenu}
        data-session-id={session.session_id}
      >
        <div className="session-text">
          <div className="session-title-row">
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
              <span className="session-icon-wrap" data-tooltip="Running in background">
                <span className="session-bg-streaming-dot" />
              </span>
            )}
            {/* Unread badge — shown after stream completes and user hasn't visited this session */}
            {unreadCount > 0 && !isActive && !bgStreaming && (
              <span className="session-unread-badge" data-tooltip={`${unreadCount} new response(s)`}>
                +{unreadCount}
              </span>
            )}
          </div>
        </div>

        <div className="session-actions">
          {session.pinned && (
            <span className="session-pin-indicator" data-tooltip={t('sessions.pinned')}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="22" x2="12" y2="13"/>
                <line x1="7" y1="13" x2="17" y2="13"/>
                <line x1="7" y1="9"  x2="17" y2="9"/>
                <line x1="7" y1="5"  x2="17" y2="5"/>
                <line x1="12" y1="5"  x2="12" y2="2"/>
              </svg>
            </span>
          )}
          {platformIcon}
          {archivedChildren && archivedChildren.length > 0 && (
            <span
              className="session-archived-badge"
              data-tooltip={`${archivedChildren.length} snapshot${archivedChildren.length > 1 ? 's' : ''}`}
              onClick={(e) => { e.stopPropagation(); setArchivedExpanded(!archivedExpanded); }}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
              </svg>
            </span>
          )}
          <span className="session-time">{relativeTime}</span>
          {session.is_cli_session || session._readonly ? (
            <span className="session-actions-trigger" style={{cursor: 'default', opacity: 0.45}} title={session.is_cli_session ? t('sessions.readonly_gateway') : t('sessions.readonly_snapshot')}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
                <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
              </svg>
            </span>
          ) : (
            <button
              ref={buttonRef}
              className={`session-actions-trigger ${menuOpen ? 'active' : ''}`}
              onClick={(e) => {
                e.stopPropagation();
                handleContextMenu(e);
              }}
              aria-label={t('sessions.actions')}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="1" />
                <circle cx="12" cy="5" r="1" />
                <circle cx="12" cy="19" r="1" />
              </svg>
            </button>
          )}
        </div>

        {menuOpen && createPortal(
          <div
            ref={menuRef}
            className="session-action-menu open"
            style={{
              position: 'fixed',
              top: menuPosition.top,
              left: menuPosition.left,
              minWidth: 140,
            }}
          >
            {!session.is_cli_session && (
              <>
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
              </>
            )}
            {!session.is_cli_session && (session.message_count ?? 0) > 50 && (
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
            {!session.is_cli_session && (session.message_count ?? 0) > 0 && (
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
            {!session.is_cli_session && (
              <button className="session-action-opt danger" onClick={handleDelete}>
                <span className="ws-opt-action">
                  <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
                  </svg>
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
        <div className="session-archived-section">
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
                        <span className="session-archived-icon" data-tooltip="Archived snapshot">
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                            <polyline points="14 2 14 8 20 8"/>
                            <line x1="16" y1="13" x2="8" y2="13"/>
                            <line x1="16" y1="17" x2="8" y2="17"/>
                          </svg>
                        </span>
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
