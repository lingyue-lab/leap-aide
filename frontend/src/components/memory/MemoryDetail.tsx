import { useEffect, useState } from 'react';
import { useMemoryStore } from '@/stores/useMemoryStore';
import type { MemoryFile } from '@/stores/useMemoryStore';
import FileView from '@/components/shared/FileView';
import { Icon } from '@/components/common/Icons';

const FILE_LABELS: Record<MemoryFile, { filename: string; title: string }> = {
  memory: { filename: 'USER.md', title: 'Personal Memory' },
  project: { filename: 'MEMORY.md', title: 'Project Memory' },
};

export default function MemoryDetail() {
  const selectedFile = useMemoryStore((s) => s.selectedFile);
  const memoryData = useMemoryStore((s) => s.memoryData);
  const loading = useMemoryStore((s) => s.loading);
  const error = useMemoryStore((s) => s.error);
  const loadMemory = useMemoryStore((s) => s.loadMemory);
  const writeMemory = useMemoryStore((s) => s.writeMemory);

  const [editing, setEditing] = useState(false);
  const [editContent, setEditContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');

  // Guard: rendered by MainContent only when selectedFile is set,
  // but protect against direct mount with null selection.
  if (!selectedFile) return null;

  useEffect(() => {
    if (!memoryData) {
      loadMemory();
    }
  }, []);

  // Reset editing state when switching files
  useEffect(() => {
    setEditing(false);
    setEditContent('');
    setSaveError('');
  }, [selectedFile]);

  const info = FILE_LABELS[selectedFile];
  const section = selectedFile === 'memory' ? memoryData?.user_memory : memoryData?.project_memory;
  const content = section?.content ?? '';

  const handleEdit = () => {
    setEditContent(content);
    setEditing(true);
    setSaveError('');
  };

  const handleCancel = () => {
    setEditing(false);
    setEditContent('');
    setSaveError('');
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveError('');
    try {
      await writeMemory(selectedFile, editContent);
      setEditing(false);
      setEditContent('');
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Topbar — hidden when editing (EditorView provides its own toolbar) */}
      {!editing && (
        <div className="topbar">
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <Icon name="description" size={18} className="flex-shrink-0 text-[var(--muted)]" />
            <div className="topbar-title font-mono">{info.filename}</div>
            <div className="topbar-meta">{info.title}</div>
          </div>

          <div className="flex items-center gap-2">
            <button
              className="icon-btn"
              onClick={handleEdit}
              disabled={loading}
              title="Edit"
            >
              <Icon name="edit" size={18} />
            </button>
          </div>
        </div>
      )}

      {/* Content body — delegated to FileView */}
      <FileView
        content={content}
        fileType="markdown"
        filePath={info.filename}
        loading={loading}
        error={error}
        editing={editing}
        editContent={editContent}
        onEditChange={setEditContent}
        onSave={handleSave}
        onCancel={handleCancel}
        saving={saving}
        editError={saveError}
        emptyMessage={`${info.filename} is empty. Click Edit to add content.`}
      />
    </div>
  );
}
