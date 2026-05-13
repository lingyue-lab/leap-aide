import { useEffect, useState } from 'react';
import { skillsService } from '@/services/api';
import { renderMarkdown } from '@/utils/markdown';
import type { SkillFileNode } from '@/types';

// ── Props ────────────────────────────────────────────────────

interface SkillFilesDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  skillDirName: string;
  activeFile: string | null;
  onFileSelect: (path: string | null) => void;
}

// ── 文件树节点 ────────────────────────────────────────────────

interface TreeNodeProps {
  node: SkillFileNode;
  indent: number;
  activeFile: string | null;
  onFileClick: (node: SkillFileNode) => void;
}

function SkillFileTreeNode({ node, indent, activeFile, onFileClick }: TreeNodeProps) {
  const [expanded, setExpanded] = useState(true);

  if (node.is_dir) {
    return (
      <div>
        <div
          className="skill-file-item flex items-center gap-1 cursor-pointer select-none"
          style={{ paddingLeft: 8 + indent * 12 }}
          onClick={() => setExpanded(!expanded)}
        >
          <svg
            width="10"
            height="10"
            viewBox="0 0 10 6"
            className={`flex-shrink-0 transition-transform ${expanded ? '' : '-rotate-90'}`}
            style={{ opacity: 0.6 }}
          >
            <path d="M1 1 L5 5 L9 1" fill="none" stroke="currentColor" strokeWidth="1.5" />
          </svg>
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="flex-shrink-0 text-[var(--gold)]"
          >
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" />
          </svg>
          <span className="truncate text-sm">{node.name}</span>
        </div>
        {expanded &&
          node.children?.map((child) => (
            <SkillFileTreeNode
              key={child.path}
              node={child}
              indent={indent + 1}
              activeFile={activeFile}
              onFileClick={onFileClick}
            />
          ))}
      </div>
    );
  }

  const isActive = activeFile === node.path;
  return (
    <div
      className={`skill-file-item flex items-center gap-1 cursor-pointer ${isActive ? 'bg-[var(--hover-bg)]' : ''}`}
      style={{ paddingLeft: 8 + indent * 12 }}
      onClick={() => onFileClick(node)}
    >
      <span className="w-[10px] flex-shrink-0" />
      <svg
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        className={`flex-shrink-0 ${isActive ? 'text-[var(--blue)]' : 'text-[var(--muted)]'}`}
      >
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
      </svg>
      <span className={`truncate text-sm flex-1 ${isActive ? 'font-medium' : ''}`}>{node.name}</span>
      {node.size !== undefined && (
        <span className="text-[10px] text-[var(--muted)] flex-shrink-0 mr-1">
          {formatSize(node.size)}
        </span>
      )}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}

// ── 文件内容区（受控，无 topbar，由 SkillDetail 统一控制） ───

export interface FileViewerProps {
  skillDirName: string;
  filePath: string;
  /** 是否处于编辑模式（由父组件控制） */
  editing: boolean;
  /** 编辑框的当前值（由父组件控制） */
  editContent: string;
  /** 编辑框值变化时通知父组件 */
  onEditChange: (val: string) => void;
  /** 文件加载完成后通知父组件：内容原文 + 是否支持文本编辑 */
  onLoad: (rawContent: string, isText: boolean) => void;
  /** 加载/保存出错时通知父组件 */
  onError: (msg: string) => void;
}

export function SkillFileViewer({
  skillDirName,
  filePath,
  editing,
  editContent,
  onEditChange,
  onLoad,
  onError,
}: FileViewerProps) {
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const isMarkdown = filePath.endsWith('.md') || filePath.endsWith('.markdown');
  const ext = filePath.split('.').pop()?.toLowerCase() || '';
  const isText = ['md', 'markdown', 'txt', 'py', 'js', 'ts', 'tsx', 'jsx', 'json', 'yaml', 'yml', 'toml', 'sh', 'bash', 'css', 'html', 'xml', 'csv'].includes(ext);

  useEffect(() => {
    setLoading(true);
    setContent(null);
    skillsService
      .getContent(skillDirName, filePath)
      .then((data) => {
        setContent(data.content);
        setLoading(false);
        onLoad(data.content, isText);
      })
      .catch((err) => {
        setLoading(false);
        onError(err instanceof Error ? err.message : 'Failed to load file');
      });
  }, [skillDirName, filePath]);

  if (loading) {
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <div className="text-sm text-[var(--muted)] text-center py-8">Loading…</div>
      </div>
    );
  }

  if (content === null) {
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <div className="text-sm text-[var(--accent)] text-center py-8">Failed to load file</div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto p-4">
      {editing ? (
        <textarea
          className="w-full h-full min-h-[200px] bg-[var(--code-bg)] text-[var(--pre-text)] font-mono text-sm p-3 rounded resize-none outline-none border border-[var(--border2)]"
          value={editContent}
          onChange={(e) => onEditChange(e.target.value)}
          spellCheck={false}
          autoFocus
        />
      ) : isMarkdown ? (
        <div
          className="prose prose-sm max-w-none text-[var(--foreground)]"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(content) }}
        />
      ) : isText ? (
        <pre className="text-sm font-mono text-[var(--pre-text)] whitespace-pre-wrap break-words bg-[var(--code-bg)] p-3 rounded">
          {content}
        </pre>
      ) : (
        <div className="text-sm text-[var(--muted)] text-center py-8 italic">Binary file – cannot preview</div>
      )}
    </div>
  );
}

// ── 主抽屉组件（仅文件树）────────────────────────────────────

export default function SkillFilesDrawer({
  isOpen,
  onClose,
  skillDirName,
  activeFile,
  onFileSelect,
}: SkillFilesDrawerProps) {
  const [tree, setTree] = useState<SkillFileNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!isOpen || !skillDirName) return;
    setLoading(true);
    setError('');
    skillsService
      .getFiles(skillDirName)
      .then((data) => {
        setTree(data.tree);
        setLoading(false);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load files');
        setLoading(false);
      });
  }, [isOpen, skillDirName]);

  if (!isOpen) return null;

  return (
    <div className="skill-files-drawer flex flex-col overflow-hidden">
      {/* header */}
      <div className="flex items-center justify-between px-2 py-1.5 border-b border-[var(--border)] bg-[var(--header-bg)] flex-shrink-0">
        <span className="text-xs font-semibold text-[var(--foreground)]">Files</span>
        <div className="flex items-center gap-1">
          <button
            className="icon-btn"
            onClick={() => {
              setLoading(true);
              setError('');
              skillsService
                .getFiles(skillDirName)
                .then((data) => { setTree(data.tree); setLoading(false); })
                .catch((err) => { setError(err instanceof Error ? err.message : 'Failed to refresh'); setLoading(false); });
            }}
            title="Refresh"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
          </button>
          <button className="icon-btn" onClick={onClose} title="Close">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      {/* file tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {loading ? (
          <div className="text-xs text-[var(--muted)] text-center py-4">Loading…</div>
        ) : error ? (
          <div className="text-xs text-[var(--accent)] text-center py-4">{error}</div>
        ) : tree.length === 0 ? (
          <div className="text-xs text-[var(--muted)] text-center py-4 italic">Empty directory</div>
        ) : (
          tree.map((node) => (
            <SkillFileTreeNode
              key={node.path}
              node={node}
              indent={0}
              activeFile={activeFile}
              onFileClick={(n) => onFileSelect(n.path)}
            />
          ))
        )}
      </div>
    </div>
  );
}
