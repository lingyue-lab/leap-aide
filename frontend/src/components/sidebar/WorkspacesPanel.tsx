import { useEffect, useRef, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSessionStore } from '@/stores/useSessionStore';
import type { Workspace } from '@/types';
import CustomScrollbar from '@/components/common/CustomScrollbar';
import { TrashMultiIcon, Icon } from '@/components/common/Icons';

export default function WorkspacesPanel() {
  const { t } = useTranslation('common');
  const {
    workspaces, currentWorkspace, loadWorkspaces, selectWorkspace,
    removeWorkspace, renameWorkspace, setShowNewWorkspaceForm, showNewWorkspaceForm,
  } = useWorkspaceStore();
  const { sessions } = useSessionStore();

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

  const startRename = (workspace: Workspace) => {
    setRenamingPath(workspace.path);
    setRenameValue(workspace.name);
    setRenameError('');
  };

  const saveRename = async () => {
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

  // Sort workspaces alphabetically by name (consistent with session panel grouping)
  const sortedWorkspaces = useMemo(
    () => [...workspaces].sort((a, b) => a.name.localeCompare(b.name)),
    [workspaces],
  );

  const cancelRename = () => {
    setRenamingPath(null);
    setRenameError('');
  };

  const handleRenameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') { e.preventDefault(); saveRename(); }
    else if (e.key === 'Escape') { cancelRename(); }
  };

  const handleDelete = async (workspace: Workspace) => {
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
      <div className="sidebar-header">
        <span className="sidebar-header-title">
          {t('workspaces.title') || 'Workspaces'}
        </span>
        <div className="sidebar-header-actions">
          <button
            className="icon-btn"
            onClick={() => setShowNewWorkspaceForm(true)}
            title="Add workspace"
          >
            <Icon name="add" size={18} />
          </button>
        </div>
      </div>

      {/* Workspace List */}
      <CustomScrollbar className="flex-1">
        {workspaces.length === 0 && (
          <div className="text-sm text-[var(--muted)] text-center italic py-4">
            {t('workspaces.empty') || 'No workspaces added'}
          </div>
        )}

        {sortedWorkspaces.map((workspace) => (
          <div
            key={workspace.path}
            className={`sidebar-item${currentWorkspace === workspace.path ? ' active' : ''}`}
            onClick={() => {
              if (renamingPath === workspace.path) return;
              if (showNewWorkspaceForm) setShowNewWorkspaceForm(false);
              selectWorkspace(workspace.path);
            }}
          >
            <div className="sidebar-item-text">
              {renamingPath === workspace.path ? (
                <div onClick={(e) => e.stopPropagation()}>
                  <input
                    ref={renameInputRef}
                    type="text"
                    value={renameValue}
                    onChange={(e) => setRenameValue(e.target.value)}
                    onKeyDown={handleRenameKeyDown}
                    onBlur={saveRename}
                    className="sidebar-item-title-input"
                  />
                  {renameError && (
                    <div className="text-sm text-[var(--accent)] mt-1">{renameError}</div>
                  )}
                </div>
              ) : (
                <>
                  <div className="sidebar-item-title-row">
                    <span className="sidebar-item-title">{workspace.name}{workspace.external ? '(EXT)' : ''}</span>
                  </div>
                  <div className="text-sm text-[var(--muted)] truncate">{workspace.path}</div>
                </>
              )}
            </div>
            {!workspace.external && renamingPath !== workspace.path && (
              <div className="sidebar-item-actions">
                <button
                  className="sidebar-item-actions-trigger"
                  onClick={(e) => { e.stopPropagation(); handleDelete(workspace); }}
                  title={t('sessions.delete')}
                >
                  <TrashMultiIcon size={16} />
                </button>
                <button
                  className="sidebar-item-actions-trigger"
                  onClick={(e) => { e.stopPropagation(); startRename(workspace); }}
                  title={t('sessions.rename')}
                >
                  <Icon name="edit" size={16} />
                </button>
              </div>
            )}
          </div>
        ))}
      </CustomScrollbar>
    </div>
  );
}
