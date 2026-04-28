import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import type { Workspace } from '@/types';

export default function WorkspacesPanel() {
  const { t } = useTranslation('common');
  const { workspaces, currentWorkspace, loadWorkspaces, selectWorkspace, addWorkspace, removeWorkspace } = useWorkspaceStore();
  const [showAddForm, setShowAddForm] = useState(false);
  const [addPath, setAddPath] = useState('');
  const [addName, setAddName] = useState('');
  const [formError, setFormError] = useState('');

  useEffect(() => {
    loadWorkspaces();
  }, [loadWorkspaces]);

  const handleAdd = async () => {
    if (!addPath.trim()) {
      setFormError('Path is required');
      return;
    }

    try {
      await addWorkspace(addPath.trim(), addName.trim() || undefined);
      setAddPath('');
      setAddName('');
      setShowAddForm(false);
      setFormError('');
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to add workspace');
    }
  };

  const handleRemove = async (workspace: Workspace) => {
    if (!confirm(t('workspaces.confirm_remove') || 'Remove this workspace?')) return;
    try {
      await removeWorkspace(workspace.path);
    } catch (err) {
      console.error('Failed to remove workspace:', err);
    }
  };

  const handleSelect = (workspace: Workspace) => {
    selectWorkspace(workspace.path);
  };

  return (
    <div className="workspaces-panel">
      {/* Header */}
      <div className="flex items-center justify-between p-2 border-b border-[var(--border)]">
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
          {t('workspaces.title') || 'Workspaces'}
        </span>
        <button
          className="cron-btn run text-xs px-2 py-1"
          onClick={() => setShowAddForm(!showAddForm)}
        >
          + {t('workspaces.add') || 'Add'}
        </button>
      </div>

      {/* Description */}
      <div className="px-2 py-1 text-xs text-[var(--muted)] border-b border-[var(--border)]">
        {t('workspaces.desc') || 'Add and switch workspaces for your sessions.'}
      </div>

      {/* Add Form */}
      {showAddForm && (
        <div className="workspace-form p-2 border-b border-[var(--border)]">
          <input
            type="text"
            placeholder={t('workspaces.path_placeholder') || 'Workspace path (e.g. /home/user/project)'}
            value={addPath}
            onChange={(e) => setAddPath(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
          />
          <input
            type="text"
            placeholder={t('workspaces.name_placeholder') || 'Display name (optional)'}
            value={addName}
            onChange={(e) => setAddName(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
          />
          <div className="flex gap-2">
            <button
              className="cron-btn run flex-1 text-xs py-1"
              onClick={handleAdd}
            >
              {t('workspaces.add') || 'Add'}
            </button>
            <button
              className="cron-btn flex-1 text-xs py-1"
              onClick={() => {
                setShowAddForm(false);
                setAddPath('');
                setAddName('');
                setFormError('');
              }}
            >
              {t('common.cancel') || 'Cancel'}
            </button>
          </div>
          {formError && (
            <div className="text-xs text-[var(--accent)] mt-2">{formError}</div>
          )}
        </div>
      )}

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
                ? 'border-[var(--accent)] bg-[var(--accent)]/10'
                : 'border-[var(--border2)] hover:bg-[var(--hover-bg)]'
            }`}
            onClick={() => handleSelect(workspace)}
          >
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="flex-shrink-0">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                </svg>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">
                    {workspace.display_name || workspace.name}
                  </div>
                  <div className="text-xs text-[var(--muted)] truncate">
                    {workspace.path}
                  </div>
                </div>
              </div>
              <button
                className="icon-btn text-[var(--accent)] flex-shrink-0 ml-2"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRemove(workspace);
                }}
                title="Remove"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
