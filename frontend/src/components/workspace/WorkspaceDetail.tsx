import { useState, useCallback, lazy, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
// Lazy-load FileTreePanel so react-arborist + react-dnd (~120KB) is only
// fetched when the user actually opens a workspace detail view.
const FileTreePanel = lazy(() => import('@/components/shared/FileTreePanel'));
import FileView from '@/components/shared/FileView';
import type { FileType } from '@/components/shared/FileView';
import type { FileNode } from '@/types';
import { FolderOpenIcon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import WorkspaceOverview from './WorkspaceOverview';

// ── Helpers ──────────────────────────────────────────────────────

function toFileType(t: string | null): FileType {
  if (!t) return 'binary';
  if (t === 'markdown' || t === 'text' || t === 'image') return t;
  return 'binary';
}

// ── Main file content viewer ──────────────────────────────────────

interface WorkspaceFileMainProps {
  showFileTree: boolean;
  onToggleFileTree: () => void;
}

function WorkspaceFileMain({ showFileTree, onToggleFileTree }: WorkspaceFileMainProps) {
  const { t } = useTranslation('common');
  const {
    previewPath, previewContent, previewType, previewLoading,
    previewEditing, updatePreviewContent,
    setPreviewEditing, savePreview, currentWorkspace,
  } = useWorkspaceStore();
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await savePreview();
    } catch (err) {
      alert(`Failed to save: ${(err as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const fileName = previewPath
    ? (currentWorkspace
        ? previewPath.replace(currentWorkspace.replace(/\/?$/, '/'), '')
        : previewPath.split('/').pop())
    : null;

  const fileType = toFileType(previewType);

  if (!currentWorkspace) return null;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* file header — hidden when editing (EditorView provides its own toolbar) */}
      {!previewEditing && (
      <div className="topbar">
        <span className="topbar-title">
          {fileName || <span className="italic text-[var(--muted)]">{t('no_file_selected')}</span>}
        </span>
        <div className="topbar-chips">
          {/* Files toggle button */}
          <button
            className={`icon-btn${showFileTree ? ' active' : ''}`}
            onClick={onToggleFileTree}
            title={showFileTree ? 'Hide files' : 'Show files'}
          >
            <FolderOpenIcon />
          </button>
        </div>
      </div>
      )}

      {/* file body */}
      <FileView
        content={previewContent}
        fileType={fileType}
        filePath={previewPath || undefined}
        loading={previewLoading}
        editing={previewEditing}
        editContent={previewContent || ''}
        onEditChange={updatePreviewContent}
        onSave={handleSave}
        onCancel={() => setPreviewEditing(false)}
        saving={saving}
        emptyMessage={t('select_file_hint')}
      />
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────

export default function WorkspaceDetail() {
  const {
    currentWorkspace,
    previewPath,
    setPreview,
    setPreviewEditing,
    deleteFile,
    createFileItem,
    renameFileItem,
    showFileTree,
    setShowFileTree,
  } = useWorkspaceStore();

  const workspaceLoader = useCallback(async (path: string) => {
    const res = await fetch(
      `/api/workspace/list?path=${encodeURIComponent(path)}`,
      { credentials: 'include' }
    );
    if (!res.ok) return [];
    const data = await res.json();
    return (data.entries as FileNode[]).sort((a, b) => {
      if (a.is_dir !== b.is_dir) return b.is_dir ? 1 : -1;
      return a.name.localeCompare(b.name);
    });
  }, []);

  const handleTreeCreate = async (parentPath: string, name: string, isDir: boolean) => {
    const fullPath = parentPath ? `${parentPath}/${name}` : `${currentWorkspace || ''}/${name}`;
    await createFileItem(fullPath.replace(/\/\//g, '/'), isDir);
  };

  const handleTreeRename = async (oldPath: string, newPath: string) => {
    await renameFileItem(oldPath, newPath);
  };

  const handleTreeDelete = async (path: string) => {
    if (!confirm(`Delete "${path.split('/').pop()}"?`)) return;
    await deleteFile(path);
  };

  const handleTreeEdit = (path: string) => {
    setPreview(path);
    setPreviewEditing(true);
  };

  return (
    <div className="flex h-full overflow-hidden">
      {previewPath ? (
        <WorkspaceFileMain
          showFileTree={showFileTree}
          onToggleFileTree={() => setShowFileTree(!showFileTree)}
        />
      ) : (
        <WorkspaceOverview />
      )}
      <Suspense fallback={<LoadingSpinner mode="inline" text="Loading files..." />}>
        <FileTreePanel
          visible={showFileTree}
          defaultWidth={280}
          minWidth={180}
          maxWidth={500}
          storageKey="hermes_workspace_files_width"
          rootPath={currentWorkspace || ''}
          loader={workspaceLoader}
          loadKey={currentWorkspace}
          defaultExpanded={false}
          activePath={previewPath}
          onFileSelect={(path) => { setPreview(path); setPreviewEditing(false); }}
          title={currentWorkspace ? currentWorkspace.split('/').pop() : 'Files'}
          showRefresh={true}
          onClose={() => setShowFileTree(false)}
          emptyMessage="Select a workspace"
          onCreate={handleTreeCreate}
          onRename={handleTreeRename}
          onDelete={handleTreeDelete}
          onEdit={handleTreeEdit}
        />
      </Suspense>
    </div>
  );
}
