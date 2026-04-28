import { useState } from 'react';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { renderMarkdown } from '@/utils/markdown';

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

  return (
    <div className="preview-panel">
      <div className="preview-header flex items-center justify-between px-3 py-2 border-b border-[var(--border)]">
        <span className="preview-title text-sm font-medium truncate flex-1">{previewPath}</span>
        <div className="preview-actions flex items-center gap-2">
          {previewType === 'text' && (
            <>
              {previewEditing ? (
                <>
                  <button
                    className="text-xs px-2 py-1 rounded bg-[var(--blue)] text-white"
                    onClick={handleSave}
                    disabled={saving}
                  >
                    {saving ? 'Saving...' : 'Save'}
                  </button>
                  <button
                    className="text-xs px-2 py-1 rounded border border-[var(--border2)]"
                    onClick={() => setPreviewEditing(false)}
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  className="text-xs px-2 py-1 rounded border border-[var(--border2)]"
                  onClick={() => setPreviewEditing(true)}
                >
                  Edit
                </button>
              )}
            </>
          )}
          <button className="icon-btn" onClick={handleClose} title="Close preview">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>
      
      <div className="preview-body flex-1 overflow-auto p-3">
        {previewLoading ? (
          <div className="flex items-center justify-center py-8">
            <svg className="animate-spin" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
              <path d="M12 2a10 10 0 0 1 10 10" />
            </svg>
          </div>
        ) : previewType === 'image' ? (
          <img
            src={previewContent || ''}
            alt={previewPath}
            className="max-w-full max-h-[60vh] mx-auto"
          />
        ) : previewType === 'markdown' && !previewEditing ? (
          <div className="preview-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(previewContent || '') }} />
        ) : previewType === 'text' || previewEditing ? (
          <textarea
            className="w-full h-[60vh] bg-[var(--code-bg)] text-[var(--pre-text)] font-mono text-xs p-3 rounded resize-none"
            value={previewContent || ''}
            onChange={(e) => updatePreviewContent(e.target.value)}
            spellCheck={false}
          />
        ) : (
          <div className="text-center text-[var(--muted)] py-8">
            Binary file - cannot preview
          </div>
        )}
      </div>
    </div>
  );
}
