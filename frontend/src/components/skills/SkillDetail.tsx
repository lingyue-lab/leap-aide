import { useEffect, useState } from 'react';
import { useSkillStore } from '@/stores/useSkillStore';
import { skillsService } from '@/services/api';
import { renderMarkdown } from '@/utils/markdown';

export default function SkillDetail() {
  const selectedSkill = useSkillStore((s) => s.selectedSkill);
  const selectedSkillContent = useSkillStore((s) => s.selectedSkillContent);
  const loadingContent = useSkillStore((s) => s.loadingContent);
  const clearSelection = useSkillStore((s) => s.clearSelection);
  const setSelectedSkillContent = useSkillStore((s) => s.setSelectedSkillContent);
  const loadSkills = useSkillStore((s) => s.loadSkills);
  const isEditing = useSkillStore((s) => s.isEditing);
  const setIsEditing = useSkillStore((s) => s.setIsEditing);

  const [editContent, setEditContent] = useState('');
  const [editError, setEditError] = useState('');

  // When store cancels editing externally (e.g. nav tab switch), reset local edit state
  useEffect(() => {
    if (!isEditing) {
      setEditError('');
    }
  }, [isEditing]);
  const [deleteError, setDeleteError] = useState('');

  if (!selectedSkill) return null;

  const handleEdit = () => {
    setEditContent(selectedSkillContent || '');
    setEditError('');
    setIsEditing(true);
  };

  const handleSave = async () => {
    if (!editContent.trim()) {
      setEditError('Content is required');
      return;
    }
    try {
      await skillsService.save(selectedSkill.dir_name || selectedSkill.name, editContent, selectedSkill.category);
      setSelectedSkillContent(editContent);
      setIsEditing(false);
      setEditError('');
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to save');
    }
  };

  const handleDelete = async () => {
    if (!confirm('Delete this skill?')) return;
    setDeleteError('');
    try {
      await skillsService.delete(selectedSkill.dir_name || selectedSkill.name);
      clearSelection();
      await loadSkills();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete skill');
    }
  };

  const handleCancel = () => {
    setIsEditing(false);
    setEditError('');
  };

  const renderedContent = selectedSkillContent ? renderMarkdown(selectedSkillContent) : '';

  // Full-screen edit mode
  if (isEditing) {
    return (
      <div className="skill-detail flex-1 flex flex-col overflow-hidden">
        {/* Edit topbar */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)] bg-[var(--header-bg)]">
          <div className="text-sm font-semibold truncate">
            {selectedSkill.label || selectedSkill.name}
            <span className="ml-2 text-xs font-normal text-[var(--muted)]">— editing</span>
          </div>
          <div className="flex gap-2 flex-shrink-0">
            {editError && (
              <span className="text-xs text-[var(--accent)] self-center">{editError}</span>
            )}
            <button className="cron-btn run text-xs px-3 py-1" onClick={handleSave}>
              Save
            </button>
            <button className="cron-btn text-xs px-3 py-1" onClick={handleCancel}>
              Cancel
            </button>
          </div>
        </div>
        {/* Full-height textarea */}
        <textarea
          className="flex-1 w-full p-4 text-sm bg-[var(--bg)] text-[var(--foreground)] font-mono resize-none outline-none border-none"
          value={editContent}
          onChange={(e) => setEditContent(e.target.value)}
          autoFocus
        />
      </div>
    );
  }

  // View mode
  return (
    <div className="skill-detail flex-1 flex flex-col overflow-hidden">
      {/* Top bar with title and actions */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)] bg-[var(--header-bg)]">
        <div className="flex items-center gap-3 min-w-0">
          <div className="min-w-0">
            <div className="text-sm font-semibold truncate">
              {selectedSkill.label || selectedSkill.name}
            </div>
            {selectedSkill.category && (
              <div className="text-xs text-[var(--muted)]">{selectedSkill.category}</div>
            )}
          </div>
        </div>

        {/* Actions: Edit & Delete in top-right */}
        <div className="flex items-center gap-1 flex-shrink-0">
          {deleteError && (
            <span className="text-xs text-[var(--accent)] self-center mr-1">{deleteError}</span>
          )}
          <button
            className="icon-btn"
            onClick={handleEdit}
            title="Edit"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
            </svg>
          </button>
          <button
            className="icon-btn text-[var(--accent)]"
            onClick={handleDelete}
            title="Delete"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* Meta info */}
        <div className="mb-3 text-sm text-[var(--muted)] space-y-1">
          <div className="flex gap-4 flex-wrap">
            <span><span className="font-medium text-[var(--foreground)]">Name:</span> {selectedSkill.name}</span>
            {selectedSkill.label && selectedSkill.label !== selectedSkill.name && (
              <span><span className="font-medium text-[var(--foreground)]">Label:</span> {selectedSkill.label}</span>
            )}
            {selectedSkill.category && (
              <span><span className="font-medium text-[var(--foreground)]">Category:</span> {selectedSkill.category}</span>
            )}
            {selectedSkill.created_at && (
              <span><span className="font-medium text-[var(--foreground)]">Created:</span> {new Date(selectedSkill.created_at * 1000).toLocaleString()}</span>
            )}
            {selectedSkill.updated_at && (
              <span><span className="font-medium text-[var(--foreground)]">Updated:</span> {new Date(selectedSkill.updated_at * 1000).toLocaleString()}</span>
            )}
          </div>
          {selectedSkill.description && (
            <div><span className="font-medium text-[var(--foreground)]">Description:</span> {selectedSkill.description}</div>
          )}
        </div>
        <hr className="border-[var(--border)] mb-4" />
        {loadingContent ? (
          <div className="text-sm text-[var(--muted)] text-center py-8">Loading...</div>
        ) : renderedContent ? (
          <div
            className="prose prose-sm max-w-none text-[var(--foreground)]"
            dangerouslySetInnerHTML={{ __html: renderedContent }}
          />
        ) : (
          <div className="text-sm text-[var(--muted)] text-center py-8 italic">
            No content
          </div>
        )}
      </div>
    </div>
  );
}
