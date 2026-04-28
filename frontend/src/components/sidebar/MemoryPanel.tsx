import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { memoryService } from '@/services/api';
import type { MemoryData } from '@/types';

export default function MemoryPanel() {
  const { t } = useTranslation('common');
  const [memoryData, setMemoryData] = useState<MemoryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingSection, setEditingSection] = useState<'memory' | 'project' | null>(null);
  const [editContent, setEditContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');

  useEffect(() => {
    loadMemory();
  }, []);

  const loadMemory = async () => {
    try {
      setLoading(true);
      const data = await memoryService.get();
      setMemoryData(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load memory');
    } finally {
      setLoading(false);
    }
  };

  const toggleEdit = (section: 'memory' | 'project', currentContent?: string) => {
    if (editingSection === section) {
      setEditingSection(null);
      setEditContent('');
      setSaveError('');
    } else {
      setEditingSection(section);
      setEditContent(currentContent || '');
      setSaveError('');
    }
  };

  const handleSave = async (section: 'memory' | 'project') => {
    try {
      setSaving(true);
      setSaveError('');
      await memoryService.write(section, editContent);
      loadMemory();
      setEditingSection(null);
      setEditContent('');
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="p-3 text-center text-[var(--muted)] text-sm">
        {t('common.loading') || 'Loading...'}
      </div>
    );
  }

  const userMemory = memoryData?.user_memory;
  const projectMemory = memoryData?.project_memory;

  return (
    <div className="memory-panel">
      {/* User Memory Section */}
      <div className="memory-section border-b border-[var(--border)]">
        <div className="flex items-center justify-between p-2">
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
            {t('memory.user') || 'Personal Memory'}
          </span>
          {editingSection !== 'memory' && (
            <button
              className="text-xs text-[var(--accent)] flex items-center gap-1"
              onClick={() => toggleEdit('memory', userMemory?.content)}
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
              </svg>
              {t('memory.edit') || 'Edit'}
            </button>
          )}
        </div>
        {editingSection === 'memory' ? (
          <div className="px-2 pb-2">
            <textarea
              rows={5}
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              className="w-full px-2 py-1 text-xs bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded font-mono resize-vertical"
              placeholder={t('memory.placeholder') || 'Enter memory content...'}
            />
            <div className="flex gap-2 mt-2">
              <button
                className="cron-btn run flex-1 text-xs py-1"
                onClick={() => handleSave('memory')}
                disabled={saving}
              >
                {saving ? (t('common.saving') || 'Saving...') : (t('common.save') || 'Save')}
              </button>
              <button
                className="cron-btn flex-1 text-xs py-1"
                onClick={() => toggleEdit('memory')}
                disabled={saving}
              >
                {t('common.cancel') || 'Cancel'}
              </button>
            </div>
            {saveError && (
              <div className="text-xs text-[var(--accent)] mt-1">{saveError}</div>
            )}
          </div>
        ) : (
          <div className="px-2 pb-3">
            {userMemory?.content ? (
              <div className="memory-content text-xs whitespace-pre-wrap text-[var(--text)]">
                {userMemory.content}
              </div>
            ) : (
              <div className="text-xs text-[var(--muted)] italic">
                {t('memory.no_memory') || 'No memory content'}
              </div>
            )}
            {userMemory?.updated_at && (
              <div className="text-[10px] text-[var(--muted)] mt-1">
                {new Date(userMemory.updated_at * 1000).toLocaleString()}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Project Memory Section */}
      <div className="memory-section">
        <div className="flex items-center justify-between p-2">
          <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
            {t('memory.project') || 'Project Memory'}
          </span>
          {editingSection !== 'project' && (
            <button
              className="text-xs text-[var(--accent)] flex items-center gap-1"
              onClick={() => toggleEdit('project', projectMemory?.content)}
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
              </svg>
              {t('memory.edit') || 'Edit'}
            </button>
          )}
        </div>
        {editingSection === 'project' ? (
          <div className="px-2 pb-2">
            <textarea
              rows={5}
              value={editContent}
              onChange={(e) => setEditContent(e.target.value)}
              className="w-full px-2 py-1 text-xs bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded font-mono resize-vertical"
              placeholder={t('memory.placeholder') || 'Enter memory content...'}
            />
            <div className="flex gap-2 mt-2">
              <button
                className="cron-btn run flex-1 text-xs py-1"
                onClick={() => handleSave('project')}
                disabled={saving}
              >
                {saving ? (t('common.saving') || 'Saving...') : (t('common.save') || 'Save')}
              </button>
              <button
                className="cron-btn flex-1 text-xs py-1"
                onClick={() => toggleEdit('project')}
                disabled={saving}
              >
                {t('common.cancel') || 'Cancel'}
              </button>
            </div>
            {saveError && (
              <div className="text-xs text-[var(--accent)] mt-1">{saveError}</div>
            )}
          </div>
        ) : (
          <div className="px-2 pb-3">
            {projectMemory?.content ? (
              <div className="memory-content text-xs whitespace-pre-wrap text-[var(--text)]">
                {projectMemory.content}
              </div>
            ) : (
              <div className="text-xs text-[var(--muted)] italic">
                {t('memory.no_memory') || 'No memory content'}
              </div>
            )}
            {projectMemory?.updated_at && (
              <div className="text-[10px] text-[var(--muted)] mt-1">
                {new Date(projectMemory.updated_at * 1000).toLocaleString()}
              </div>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className="p-2 text-xs text-[var(--accent)]">{error}</div>
      )}
    </div>
  );
}
