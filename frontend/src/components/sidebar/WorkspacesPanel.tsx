import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSessionStore } from '@/stores/useSessionStore';
import type { Workspace } from '@/types';

export default function WorkspacesPanel() {
  const { t } = useTranslation('common');
  const {
    workspaces, currentWorkspace, loadWorkspaces, selectWorkspace,
    removeWorkspace, renameWorkspace, setShowNewWorkspaceForm, showNewWorkspaceForm,
  } = useWorkspaceStore();
  const { sessions } = useSessionStore();

  // per-workspace menu state
  const [menuOpenPath, setMenuOpenPath] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
  const menuRef = useRef<HTMLDivElement>(null);

  // rename state
  const [renamingPath, setRenamingPath] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renameError, setRenameError] = useState('');
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadWorkspaces();
  }, [loadWorkspaces]);

  useEffect(() => {
    if (renamingPath && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingPath]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpenPath(null);
      }
    };
    if (menuOpenPath) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [menuOpenPath]);

  const handleMenuOpen = (e: React.MouseEvent, workspace: Workspace) => {
    e.stopPropagation();
    const btn = e.currentTarget as HTMLElement;
    const rect = btn.getBoundingClientRect();
    setMenuPosition({ top: rect.bottom + window.scrollY, left: rect.left + window.scrollX });
    setMenuOpenPath(menuOpenPath === workspace.path ? null : workspace.path);
  };

  const handleStartRename = (workspace: Workspace) => {
    setMenuOpenPath(null);
    setRenamingPath(workspace.path);
    setRenameValue(workspace.name);
    setRenameError('');
  };

  const handleSaveRename = async () => {
    if (!renamingPath) return;
    const trimmed = renameValue.trim();
    if (!trimmed) {
      setRenameError('Name is required');
      return;
    }
    try {
      await renameWorkspace(renamingPath, trimmed);
      setRenamingPath(null);
      setRenameError('');
    } catch (err) {
      setRenameError(err instanceof Error ? err.message : 'Failed to rename');
    }
  };

  const handleCancelRename = () => {
    setRenamingPath(null);
    setRenameError('');
  };

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') { e.preventDefault(); handleSaveRename(); }
    else if (e.key === 'Escape') { handleCancelRename(); }
  };

  const handleDelete = async (workspace: Workspace) => {
    setMenuOpenPath(null);
    const using = sessions.filter((s) => s.workspace === workspace.path);
    if (using.length > 0) {
      const names = using.map((s) => `"${s.title}"`).join(', ');
      alert(`Cannot remove workspace: it is used by ${using.length} session${using.length > 1 ? 's' : ''}: ${names}`);
      return;
    }
    if (!confirm(t('workspaces.confirm_remove') || 'Remove this workspace?')) return;
    try {
      await removeWorkspace(workspace.path);
    } catch (err) {
      console.error('Failed to remove workspace:', err);
    }
  };

  return (
    <div className="workspaces-panel">
      {/* Header */}
      <div className="flex items-center justify-between p-2 border-b border-[var(--border)]">
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
          {t('workspaces.title') || 'Workspaces'}
        </span>
        <button
          className="session-actions-trigger"
          style={{ opacity: 1, width: 24, height: 24 }}
          onClick={() => setShowNewWorkspaceForm(true)}
          title="Add workspace"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>
      </div>

      {/* Description */}
      <div className="px-2 py-1 text-xs text-[var(--muted)] border-b border-[var(--border)]">
        {t('workspaces.desc') || 'Add and switch workspaces for your sessions.'}
      </div>

      {/* Workspace List */}
      <div className="workspace-list flex-1 overflow-y-auto p-2">
        {workspaces.length === 0 && (
          <div className="text-sm text-[var(--muted)] text-center italic py-4">
            {t('workspaces.empty') || 'No workspaces added'}
          </div>
        )}
        {workspaces.map((workspace) => (
          <div
            key={workspace.path}
            className={`workspace-item p-2 mb-2 rounded-lg border cursor-pointer transition-colors ${
              currentWorkspace === workspace.path
                ? 'border-transparent bg-[var(--hover-bg)]'
                : 'border-transparent hover:bg-[color-mix(in_srgb,var(--hover-bg)_50%,transparent)]'
            }`}
            onClick={() => {
              if (renamingPath === workspace.path) return;
              if (showNewWorkspaceForm) setShowNewWorkspaceForm(false);
              selectWorkspace(workspace.path);
            }}
          >
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="flex-shrink-0">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                </svg>
                <div className="flex-1 min-w-0">
                  {renamingPath === workspace.path ? (
                    <div onClick={(e) => e.stopPropagation()}>
                      <input
                        ref={renameInputRef}
                        type="text"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={handleRenameKeyDown}
                        onBlur={handleSaveRename}
                        className="w-full px-1 py-0.5 text-sm bg-[rgba(255,255,255,0.08)] border border-[var(--accent)] rounded outline-none"
                      />
                      {renameError && (
                        <div className="text-xs text-[var(--accent)] mt-1">{renameError}</div>
                      )}
                    </div>
                  ) : (
                    <>
                      <div className="text-sm font-medium truncate">{workspace.name}</div>
                      <div className="text-xs text-[var(--muted)] truncate">{workspace.path}</div>
                    </>
                  )}
                </div>
              </div>

              {/* Actions trigger */}
              <div className="flex-shrink-0 ml-1">
                <button
                  className={`session-actions-trigger ${menuOpenPath === workspace.path ? 'active' : ''}`}
                  onClick={(e) => handleMenuOpen(e, workspace)}
                  aria-label="More actions"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="1" />
                    <circle cx="12" cy="5" r="1" />
                    <circle cx="12" cy="19" r="1" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Workspace action menu */}
      {menuOpenPath && (
        <div
          ref={menuRef}
          className="session-action-menu open"
          style={{
            position: 'fixed',
            top: menuPosition.top,
            left: menuPosition.left,
            minWidth: 140,
            zIndex: 9999,
          }}
        >
          <button
            className="session-action-opt"
            onClick={() => {
              const ws = workspaces.find((w) => w.path === menuOpenPath);
              if (ws) handleStartRename(ws);
            }}
          >
            <span className="ws-opt-action">
              <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
              </svg>
              <span className="ws-opt-name">Rename</span>
            </span>
          </button>
          <button
            className="session-action-opt danger"
            onClick={() => {
              const ws = workspaces.find((w) => w.path === menuOpenPath);
              if (ws) handleDelete(ws);
            }}
          >
            <span className="ws-opt-action">
              <svg className="ws-opt-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
              </svg>
              <span className="ws-opt-name">Delete</span>
            </span>
          </button>
        </div>
      )}
    </div>
  );
}
