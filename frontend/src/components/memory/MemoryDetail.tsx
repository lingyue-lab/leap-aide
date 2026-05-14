import { useEffect, useState } from 'react';
import { useMemoryStore } from '@/stores/useMemoryStore';
import type { MemoryFile } from '@/stores/useMemoryStore';

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
  const updatedAt = section?.updated_at;

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
      {/* Topbar */}
      <div className="topbar">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="flex-shrink-0 text-[var(--muted)]"
          >
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
          </svg>
          <div className="topbar-title font-mono">{info.filename}</div>
          <div className="topbar-meta">{info.title}</div>
        </div>

        <div className="flex items-center gap-2">
          {!editing && (
            <button
              className="cron-btn run text-xs px-3 py-1"
              onClick={handleEdit}
              disabled={loading}
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="inline mr-1">
                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
              </svg>
              Edit
            </button>
          )}
          {editing && (
            <>
              <button
                className="cron-btn run text-xs px-3 py-1"
                onClick={handleSave}
                disabled={saving}
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
              <button
                className="cron-btn text-xs px-3 py-1"
                onClick={handleCancel}
                disabled={saving}
              >
                Cancel
              </button>
            </>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4">
        {loading && (
          <div className="text-sm text-[var(--muted)] text-center mt-8">Loading...</div>
        )}
        {error && !loading && (
          <div className="text-sm text-[var(--accent)] p-2">{error}</div>
        )}
        {saveError && (
          <div className="text-sm text-[var(--accent)] mb-2">{saveError}</div>
        )}

        {!loading && editing && (
          <textarea
            className="w-full h-full min-h-[400px] px-3 py-2 text-sm bg-[rgba(255,255,255,0.04)] border border-[var(--border2)] rounded font-mono resize-none focus:outline-none focus:border-[var(--accent)]"
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            placeholder={`Enter content for ${info.filename}...`}
            autoFocus
          />
        )}

        {!loading && !editing && (
          <>
            {content ? (
              <pre className="text-sm whitespace-pre-wrap text-[var(--text)] font-mono leading-relaxed">
                {content}
              </pre>
            ) : (
              <div className="text-sm text-[var(--muted)] italic text-center mt-8">
                {info.filename} is empty. Click Edit to add content.
              </div>
            )}
            {updatedAt && (
              <div className="text-[11px] text-[var(--muted)] mt-4">
                Last modified: {new Date(updatedAt * 1000).toLocaleString()}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
