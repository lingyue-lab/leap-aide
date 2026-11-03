import { useCallback, useEffect, useState, lazy, Suspense } from 'react';
import { useTranslation } from 'react-i18next';
import { useSkillStore } from '@/stores/useSkillStore';
import { skillsService } from '@/services/api';
import { renderMarkdown, relativeFileLinkTarget } from '@/utils/markdown';
// Lazy-load FileTreePanel so react-arborist + react-dnd (~120KB) is only
// fetched when the user actually opens a skill detail view.
const FileTreePanel = lazy(() => import('@/components/shared/FileTreePanel'));
import FileView, { detectFileType } from '@/components/shared/FileView';
import type { FileType } from '@/components/shared/FileView';
import EditorView from '@/components/shared/EditorView';
import CodeCopyButton from '@/components/shared/CodeCopyButton';
import { FolderOpenIcon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';

export default function SkillDetail() {
  const { t } = useTranslation('common');
  const selectedSkill = useSkillStore((s) => s.selectedSkill);
  const selectedSkillContent = useSkillStore((s) => s.selectedSkillContent);
  const loadingContent = useSkillStore((s) => s.loadingContent);
  const setSelectedSkillContent = useSkillStore((s) => s.setSelectedSkillContent);
  const isEditing = useSkillStore((s) => s.isEditing);
  const setIsEditing = useSkillStore((s) => s.setIsEditing);

  // ── SKILL.md 编辑状态 ──
  const [editContent, setEditContent] = useState('');
  const [editError, setEditError] = useState('');

  // ── Files 抽屉 & 当前选中的非-SKILL.md 文件 ──
  const showFiles = useSkillStore((s) => s.showFiles);
  const setShowFiles = useSkillStore((s) => s.setShowFiles);
  // null 或 'SKILL.md' → 显示 SKILL.md 视图；其他路径 → 显示对应文件
  const [activeFile, setActiveFile] = useState<string | null>(null);

  // ── 当前 activeFile 的受控编辑状态（由 SkillDetail topbar 统一管理） ──
  const [fileEditing, setFileEditing] = useState(false);
  const [fileEditContent, setFileEditContent] = useState('');
  const [fileSaving, setFileSaving] = useState(false);
  const [fileError, setFileError] = useState('');

  // 切换文件时自动退出所有编辑模式
  const handleFileSelect = (path: string) => {
    setActiveFile(path);
    setFileEditing(false);
    setIsEditing(false);
  };

  // ── 非-SKILL.md 文件内容 & 类型 ──
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileType, setFileType] = useState<FileType>('text');

  // ── Skill file tree (eager mode) ──
  const [skillTree, setSkillTree] = useState<any[]>([]);

  const skillDirName = selectedSkill?.dir_name || selectedSkill?.name || '';

  const loadSkillFiles = useCallback(async () => {
    if (!skillDirName) return;
    try {
      const data = await skillsService.getFiles(skillDirName);
      setSkillTree(data.tree);
    } catch (_) { /* silent */ }
  }, [skillDirName]);

  useEffect(() => {
    if (showFiles && skillDirName) loadSkillFiles();
  }, [showFiles, skillDirName, loadSkillFiles]);

  // 切换 skill 时清空 activeFile 和文件编辑状态
  useEffect(() => {
    setActiveFile(null);
    setFileEditing(false);
    setFileEditContent('');
    setFileError('');
    setFileContent(null);
  }, [selectedSkill?.name]);

  // 切换 activeFile 时加载文件内容，重置编辑状态
  useEffect(() => {
    setFileEditing(false);
    setFileEditContent('');
    setFileError('');
    setFileContent(null);

    if (!activeFile || activeFile === 'SKILL.md' || !skillDirName) return;

    const type = detectFileType(activeFile);
    setFileType(type);

    setFileLoading(true);
    skillsService
      .getContent(skillDirName, activeFile)
      .then((data) => {
        setFileContent(data.content);
        setFileEditContent(data.content);
      })
      .catch((err) => {
        setFileError(err instanceof Error ? err.message : t('skills.failed_to_load_file'));
      })
      .finally(() => setFileLoading(false));
  }, [activeFile, skillDirName]);

  // When store cancels editing externally (e.g. nav tab switch), reset local edit state
  useEffect(() => {
    if (!isEditing) {
      setEditError('');
    }
  }, [isEditing]);

  if (!selectedSkill) return null;

  // 是否在看 SKILL.md（包含 null 和显式点击 SKILL.md）
  const isSkillMdView = !activeFile || activeFile === 'SKILL.md';

  // ── SKILL.md 操作 ──
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

  // ── 其他文件操作 ──
  const handleFileSave = async () => {
    if (!activeFile || isSkillMdView) return;
    setFileSaving(true);
    setFileError('');
    try {
      await skillsService.saveFile(skillDirName, activeFile, fileEditContent);
      setFileContent(fileEditContent);
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

  // 相对文件链接（如 [中文](SKILL.zh-CN.md)）在 SPA 内是非法路由，
  // 点击时改为打开内置文件视图。
  const handleSkillMdLinkClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = relativeFileLinkTarget(e);
    if (!target) return;
    e.preventDefault();
    handleFileSelect(target);
  };

  // ── FileTreePanel 文件操作回调 ──────────────────────────────────

  const handleTreeCreate = async (parentPath: string, name: string, isDir: boolean) => {
    const fullPath = parentPath ? `${parentPath}/${name}` : name;
    await skillsService.createFile(skillDirName, fullPath, '', isDir);
    loadSkillFiles();
  };

  const handleTreeRename = async (oldPath: string, newPath: string) => {
    await skillsService.renameFile(skillDirName, oldPath, newPath);
    if (activeFile === oldPath) setActiveFile(newPath);
    loadSkillFiles();
  };

  const handleTreeDelete = async (path: string, _isDir: boolean) => {
    await skillsService.deleteFile(skillDirName, path);
    if (activeFile === path) { setActiveFile(null); setFileContent(null); }
    loadSkillFiles();
  };

  const handleTreeEdit = (path: string) => {
    // SKILL.md uses the store's isEditing (full-screen editor)
    if (path === 'SKILL.md' || path.endsWith('/SKILL.md')) {
      setEditContent(selectedSkillContent || '');
      setEditError('');
      setIsEditing(true);
      return;
    }
    // Regular files use local fileEditing state
    if (activeFile !== path) setActiveFile(path);
    setFileEditing(true);
  };

  const treeCallbacks = {
    onCreate: handleTreeCreate,
    onRename: handleTreeRename,
    onDelete: handleTreeDelete,
    onEdit: handleTreeEdit,
  };

  // ── 编辑 SKILL.md 模式 ────────────────────────────────────────
  if (isEditing) {
    return (
      <div className="skill-detail flex-1 flex flex-row overflow-hidden">
        <EditorView
          content={editContent}
          onChange={setEditContent}
          fileName="SKILL.md"
          language="markdown"
          onSave={handleSkillSave}
          onCancel={handleSkillCancel}
          error={editError}
        />
        <Suspense fallback={<LoadingSpinner mode="inline" text="Loading files..." />}>
          <FileTreePanel
            visible={showFiles}
            tree={skillTree}
            defaultExpanded={true}
            activePath={activeFile}
            onFileSelect={handleFileSelect}
            title="Files"
            showRefresh={true}
            onRefresh={loadSkillFiles}
            onClose={() => setShowFiles(false)}
            {...treeCallbacks}
          />
        </Suspense>
      </div>
    );
  }

  // ── 统一 topbar（查看/文件编辑模式共用） ─────────────────────

  const topBar = (
    <div className="topbar">
      <div className="flex-1 min-w-0 overflow-hidden">
        <div className="topbar-title">{selectedSkill.label || selectedSkill.name}</div>
        {selectedSkill.category && (
          <div className="topbar-meta">{selectedSkill.category}</div>
        )}
      </div>

      <div className="topbar-chips">
        {/* Files 按钮（始终在最后） */}
        <button
          className={`icon-btn${showFiles ? ' active' : ''}`}
          onClick={() => setShowFiles(!showFiles)}
          title={showFiles ? 'Hide files' : 'Show files'}
        >
          <FolderOpenIcon />
        </button>
      </div>
    </div>
  );

  // ── 查看/文件编辑 主视图 ──────────────────────────────────────
  return (
    <div className="skill-detail flex-1 flex flex-row overflow-hidden">
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* 编辑模式下隐藏 topbar，由 EditorView 自带工具栏接管 */}
        {!fileEditing && topBar}

        {/* 主内容区 */}
        {isSkillMdView ? (
          // SKILL.md 视图（与直接从列表点进来完全一致）
          <div className="flex-1 overflow-y-auto p-4">
            <div className="max-w-[768px] mx-auto">
            <div className="mb-3 text-base text-[var(--muted)] space-y-1">
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
              <div className="flex items-center justify-center py-8">
                <LoadingSpinner mode="inline" />
              </div>
            ) : renderedContent ? (
              <div className="file-view-code">
                <CodeCopyButton content={selectedSkillContent || ''} />
                <div
                  className="prose prose-sm text-[var(--foreground)]"
                  onClick={handleSkillMdLinkClick}
                  dangerouslySetInnerHTML={{ __html: renderedContent }}
                />
              </div>
            ) : (
              <div className="text-sm text-[var(--muted)] text-center py-8 italic">
                No content
              </div>
            )}
            </div>
          </div>
        ) : (
          // 其他文件视图
          <FileView
            content={fileContent}
            fileType={fileType}
            filePath={activeFile!}
            loading={fileLoading}
            error={fileError}
            editing={fileEditing}
            editContent={fileEditContent}
            onEditChange={setFileEditContent}
            onSave={handleFileSave}
            onCancel={handleFileCancel}
            saving={fileSaving}
            editError={fileError}
            onRelativeLink={handleFileSelect}
          />
        )}
      </div>

      {/* Files 抽屉（仅文件树） */}
      <Suspense fallback={<LoadingSpinner mode="inline" text="Loading files..." />}>
        <FileTreePanel
          visible={showFiles}
          tree={skillTree}
          defaultExpanded={true}
          activePath={activeFile}
          onFileSelect={handleFileSelect}
          title="Files"
          showRefresh={true}
          onRefresh={loadSkillFiles}
          onClose={() => setShowFiles(false)}
          {...treeCallbacks}
        />
      </Suspense>
    </div>
  );
}
