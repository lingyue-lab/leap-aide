import { useEffect, useState } from 'react';
import { useSkillStore } from '@/stores/useSkillStore';
import { skillsService } from '@/services/api';
import { renderMarkdown } from '@/utils/markdown';
import SkillFilesDrawer, { SkillFileViewer } from './SkillFilesDrawer';

export default function SkillDetail() {
  const selectedSkill = useSkillStore((s) => s.selectedSkill);
  const selectedSkillContent = useSkillStore((s) => s.selectedSkillContent);
  const loadingContent = useSkillStore((s) => s.loadingContent);
  const clearSelection = useSkillStore((s) => s.clearSelection);
  const setSelectedSkillContent = useSkillStore((s) => s.setSelectedSkillContent);
  const loadSkills = useSkillStore((s) => s.loadSkills);
  const isEditing = useSkillStore((s) => s.isEditing);
  const setIsEditing = useSkillStore((s) => s.setIsEditing);

  // ── SKILL.md 编辑状态 ──
  const [editContent, setEditContent] = useState('');
  const [editError, setEditError] = useState('');
  const [deleteError, setDeleteError] = useState('');

  // ── Files 抽屉 & 当前选中的非-SKILL.md 文件 ──
  const [showFiles, setShowFiles] = useState(false);
  // null 或 'SKILL.md' → 显示 SKILL.md 视图；其他路径 → 显示对应文件
  const [activeFile, setActiveFile] = useState<string | null>(null);

  // ── 当前 activeFile 的受控编辑状态（由 SkillDetail topbar 统一管理） ──
  const [fileEditing, setFileEditing] = useState(false);
  const [fileEditContent, setFileEditContent] = useState('');
  const [fileIsText, setFileIsText] = useState(false);
  const [fileSaving, setFileSaving] = useState(false);
  const [fileError, setFileError] = useState('');
  // 用于触发 SkillFileViewer 重新加载（文件切换时重置）
  const [fileKey, setFileKey] = useState(0);

  // 切换 skill 时清空 activeFile 和文件编辑状态
  useEffect(() => {
    setActiveFile(null);
    setFileEditing(false);
    setFileEditContent('');
    setFileError('');
  }, [selectedSkill?.name]);

  // 切换 activeFile 时重置文件编辑状态
  useEffect(() => {
    setFileEditing(false);
    setFileEditContent('');
    setFileError('');
    setFileKey((k) => k + 1);
  }, [activeFile]);

  // When store cancels editing externally (e.g. nav tab switch), reset local edit state
  useEffect(() => {
    if (!isEditing) {
      setEditError('');
    }
  }, [isEditing]);

  if (!selectedSkill) return null;

  const skillDirName = selectedSkill.dir_name || selectedSkill.name;
  // 是否在看 SKILL.md（包含 null 和显式点击 SKILL.md）
  const isSkillMdView = !activeFile || activeFile === 'SKILL.md';

  // ── SKILL.md 操作 ──
  const handleSkillEdit = () => {
    setEditContent(selectedSkillContent || '');
    setEditError('');
    setIsEditing(true);
  };

  const handleSkillSave = async () => {
    if (!editContent.trim()) {
      setEditError('Content is required');
      return;
    }
    try {
      await skillsService.save(skillDirName, editContent, selectedSkill.category);
      setSelectedSkillContent(editContent);
      setIsEditing(false);
      setEditError('');
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Failed to save');
    }
  };

  const handleSkillCancel = () => {
    setIsEditing(false);
    setEditError('');
  };

  const handleDelete = async () => {
    if (!confirm('Delete this skill?')) return;
    setDeleteError('');
    try {
      await skillsService.delete(skillDirName);
      clearSelection();
      await loadSkills();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Failed to delete skill');
    }
  };

  // ── 其他文件操作 ──
  const handleFileSave = async () => {
    if (!activeFile || isSkillMdView) return;
    setFileSaving(true);
    setFileError('');
    try {
      await skillsService.saveFile(skillDirName, activeFile, fileEditContent);
      setFileEditing(false);
    } catch (err) {
      setFileError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setFileSaving(false);
    }
  };

  const handleFileCancel = () => {
    setFileEditing(false);
    setFileError('');
  };

  const renderedContent = selectedSkillContent ? renderMarkdown(selectedSkillContent) : '';

  // Files 按钮图标（共用）
  const filesIcon = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 3h8l2 3h8a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" />
    </svg>
  );

  // ── 编辑 SKILL.md 模式（全屏 textarea） ──────────────────────
  if (isEditing) {
    return (
      <div className="skill-detail flex-1 flex flex-row overflow-hidden">
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)] bg-[var(--header-bg)]">
            <div className="text-sm font-semibold truncate">
              {selectedSkill.label || selectedSkill.name}
              <span className="ml-2 text-xs font-normal text-[var(--muted)]">— editing</span>
            </div>
            <div className="flex gap-2 flex-shrink-0">
              {editError && (
                <span className="text-xs text-[var(--accent)] self-center">{editError}</span>
              )}
              <button
                className={`icon-btn ${showFiles ? 'text-[var(--blue)]' : ''}`}
                onClick={() => setShowFiles(!showFiles)}
                title="Files"
              >
                {filesIcon}
              </button>
              <button className="cron-btn run text-xs px-3 py-1" onClick={handleSkillSave}>
                Save
              </button>
              <button className="cron-btn text-xs px-3 py-1" onClick={handleSkillCancel}>
                Cancel
              </button>
            </div>
          </div>
          <textarea
            className="flex-1 w-full p-4 text-sm bg-[var(--bg)] text-[var(--foreground)] font-mono resize-none outline-none border-none"
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            autoFocus
          />
        </div>
        <SkillFilesDrawer
          isOpen={showFiles}
          onClose={() => setShowFiles(false)}
          skillDirName={skillDirName}
          activeFile={activeFile}
          onFileSelect={setActiveFile}
        />
      </div>
    );
  }

  // ── 统一 topbar（查看/文件编辑模式共用） ─────────────────────
  const topBar = (
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

      <div className="flex items-center gap-1 flex-shrink-0">
        {/* 错误提示 */}
        {(deleteError || fileError) && (
          <span className="text-xs text-[var(--accent)] self-center mr-1">
            {deleteError || fileError}
          </span>
        )}

        {/* Files 按钮 */}
        <button
          className={`icon-btn ${showFiles ? 'text-[var(--blue)]' : ''}`}
          onClick={() => setShowFiles(!showFiles)}
          title="Files"
        >
          {filesIcon}
        </button>

        {/* SKILL.md 视图：显示 Edit + Delete */}
        {isSkillMdView && !fileEditing && (
          <>
            <button className="icon-btn" onClick={handleSkillEdit} title="Edit">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
              </svg>
            </button>
            <button className="icon-btn text-[var(--accent)]" onClick={handleDelete} title="Delete">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </>
        )}

        {/* 其他文件视图（查看态）：显示 Edit（仅文本文件） */}
        {!isSkillMdView && !fileEditing && fileIsText && (
          <button className="icon-btn" onClick={() => setFileEditing(true)} title="Edit">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
            </svg>
          </button>
        )}

        {/* 其他文件视图（编辑态）：显示 Save + Cancel */}
        {!isSkillMdView && fileEditing && (
          <>
            <button
              className="cron-btn run text-xs px-3 py-1"
              onClick={handleFileSave}
              disabled={fileSaving}
            >
              {fileSaving ? 'Saving…' : 'Save'}
            </button>
            <button className="cron-btn text-xs px-3 py-1" onClick={handleFileCancel}>
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  );

  // ── 查看/文件编辑 主视图 ──────────────────────────────────────
  return (
    <div className="skill-detail flex-1 flex flex-row overflow-hidden">
      <div className="flex-1 flex flex-col overflow-hidden">
        {topBar}

        {/* 主内容区 */}
        {isSkillMdView ? (
          // SKILL.md 视图（与直接从列表点进来完全一致）
          <div className="flex-1 overflow-y-auto p-4">
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
        ) : (
          // 其他文件视图（受控，由本组件管理编辑状态）
          <SkillFileViewer
            key={fileKey}
            skillDirName={skillDirName}
            filePath={activeFile!}
            editing={fileEditing}
            editContent={fileEditContent}
            onEditChange={setFileEditContent}
            onLoad={(raw, isText) => {
              setFileEditContent(raw);
              setFileIsText(isText);
            }}
            onError={(msg) => setFileError(msg)}
          />
        )}
      </div>

      {/* Files 抽屉（仅文件树） */}
      <SkillFilesDrawer
        isOpen={showFiles}
        onClose={() => setShowFiles(false)}
        skillDirName={skillDirName}
        activeFile={activeFile}
        onFileSelect={setActiveFile}
      />
    </div>
  );
}
