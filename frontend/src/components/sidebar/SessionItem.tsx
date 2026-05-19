import React, { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { formatDistanceToNow } from 'date-fns';
import { zhCN, enUS } from 'date-fns/locale';
import type { SessionListItem } from '@/types';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { isSessionStreaming, subscribe as subscribeStream } from '@/stores/streamManager';

interface SessionItemProps {
  session: SessionListItem;
  isActive: boolean;
  onClick: () => void;
}

export default function SessionItem({ session, isActive, onClick }: SessionItemProps) {
  const { t, i18n } = useTranslation('common');
  const { renameSession, deleteSession, pinSession, assignWorkspace } = useSessionStore();
  const { workspaces } = useWorkspaceStore();
  
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(session.title);
  const [menuOpen, setMenuOpen] = useState(false);
  const [showWorkspaceMenu, setShowWorkspaceMenu] = useState(false);
  /** True when this session has a live background stream */
  const [bgStreaming, setBgStreaming] = useState(() => isSessionStreaming(session.session_id));
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
  
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
    setMenuOpen(true);
  };

  const handleStartEdit = () => {
    setIsEditing(true);
    setMenuOpen(false);
  };

  const handleAssignWorkspace = () => {
    setShowWorkspaceMenu((prev) => !prev);
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

  const handlePin = async () => {
    setMenuOpen(false);
    setShowWorkspaceMenu(false);
    await pinSession(session.session_id, !session.pinned);
  };

  const relativeTime = formatDistanceToNow(new Date(session.created_at * 1000), {
    addSuffix: false,
    locale: dateLocale,
  });

  return (
    <div
      className={`session-item ${isActive ? 'active' : ''} ${bgStreaming && !isActive ? 'bg-streaming' : ''}`}
      onClick={isEditing ? undefined : onClick}
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
            minWidth: showWorkspaceMenu ? 220 : 140,
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
  );
}
