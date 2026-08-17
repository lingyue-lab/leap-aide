import { useState } from 'react';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import FileView from '@/components/shared/FileView';
import type { FileType } from '@/components/shared/FileView';
import { EditIcon, Icon } from '@/components/common/Icons';

export default function FilePreview() {
  const {
    previewPath,
    previewContent,
    previewType,
    previewLoading,
    previewDirty,
    previewEditing,
    updatePreviewContent,
    setPreviewEditing,
    savePreview,
    setPreview,
  } = useWorkspaceStore();

  const [saving, setSaving] = useState(false);

  if (!previewPath) {
    return null;
  }

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

  const handleClose = () => {
    if (previewDirty) {
      if (!confirm('Discard unsaved changes?')) return;
    }
    setPreview(null);
  };

  const fileType: FileType = previewType ?? 'binary';

  return (
    <div className="preview-panel flex-1 flex flex-col overflow-hidden min-h-0">
      <div className="topbar">
        <span className="topbar-title">{previewPath}</span>
        <div className="topbar-chips">
          {!previewEditing && previewType === 'text' && (
            <button
              className="icon-btn"
              onClick={() => setPreviewEditing(true)}
              title="Edit"
            >
              <EditIcon />
            </button>
          )}
          <button className="icon-btn" onClick={handleClose} title="Close preview">
            <Icon name="close" size={18} />
          </button>
        </div>
      </div>

      <FileView
        content={previewContent}
        fileType={fileType}
        filePath={previewPath}
        loading={previewLoading}
        editing={previewEditing}
        editContent={previewContent || ''}
        onEditChange={updatePreviewContent}
        onSave={handleSave}
        onCancel={() => setPreviewEditing(false)}
        saving={saving}
      />
    </div>
  );
}
