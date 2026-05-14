import { useEffect, useState } from 'react';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { renderMarkdown } from '@/utils/markdown';
import type { FileNode } from '@/types';

// ── Dir tree node (fetches own children) ─────────────────────────

interface DirTreeNodeProps {
  node: FileNode;
  indent: number;
  activePath: string | null;
  onFileClick: (node: FileNode) => void;
}

function DirTreeNode({ node, indent, activePath, onFileClick }: DirTreeNodeProps) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(false);

  const toggleExpand = async () => {
    if (!expanded && children.length === 0) {
      setLoading(true);
      try {
        const res = await fetch(
          `/api/workspace/list?path=${encodeURIComponent(node.path)}`,
          { credentials: 'include' }
        );
        if (res.ok) {
          const data = await res.json();
          setChildren(
            (data.entries as FileNode[]).sort((a, b) => {
              if (a.is_dir !== b.is_dir) return b.is_dir ? 1 : -1;
              return a.name.localeCompare(b.name);
            })
          );
        }
      } catch (_) {/* silent */}
      setLoading(false);
    }
    setExpanded(!expanded);
  };

  if (node.is_dir) {
    return (
      <div>
        <div
          className="skill-file-item flex items-center gap-1 cursor-pointer select-none"
          style={{ paddingLeft: 8 + indent * 12 }}
          onClick={toggleExpand}
        >
          <svg
            width="10" height="10" viewBox="0 0 10 6"
            className={`flex-shrink-0 transition-transform ${expanded ? '' : '-rotate-90'}`}
            style={{ opacity: 0.6 }}
          >
            <path d="M1 1 L5 5 L9 1" fill="none" stroke="currentColor" strokeWidth="1.5" />
          </svg>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            className="flex-shrink-0 text-[var(--gold)]">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
          </svg>
          <span className="truncate text-sm">{node.name}</span>
          {loading && <span className="text-[10px] text-[var(--muted)] ml-1">…</span>}
        </div>
        {expanded && children.map((child) => (
          <DirTreeNode key={child.path} node={child} indent={indent + 1}
            activePath={activePath} onFileClick={onFileClick} />
        ))}
      </div>
    );
  }

  const isActive = activePath === node.path;
  return (
    <div
      className={`skill-file-item flex items-center gap-1 cursor-pointer ${isActive ? 'bg-[var(--hover-bg)]' : ''}`}
      style={{ paddingLeft: 8 + indent * 12 }}
      onClick={() => onFileClick(node)}
    >
      <span className="w-[10px] flex-shrink-0" />
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        className={`flex-shrink-0 ${isActive ? 'text-[var(--blue)]' : 'text-[var(--muted)]'}`}>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
      </svg>
      <span className={`truncate text-sm flex-1 ${isActive ? 'font-medium' : ''}`}>{node.name}</span>
      {node.size !== undefined && (
        <span className="text-[10px] text-[var(--muted)] flex-shrink-0 mr-1">{formatSize(node.size)}</span>
      )}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}

// ── Right panel: workspace dir tree ──────────────────────────────

interface WorkspaceDirTreeProps {
  visible: boolean;
}

function WorkspaceDirTree({ visible }: WorkspaceDirTreeProps) {
  const { currentWorkspace, previewPath, setPreview } = useWorkspaceStore();
  const [rootEntries, setRootEntries] = useState<FileNode[]>([]);
  const [loading, setLoading] = useState(false);

  const loadRoot = async (ws: string) => {
    setLoading(true);
    setRootEntries([]);
    try {
      const res = await fetch(
        `/api/workspace/list?path=${encodeURIComponent(ws)}`,
        { credentials: 'include' }
      );
      if (res.ok) {
        const data = await res.json();
        setRootEntries(
          (data.entries as FileNode[]).sort((a, b) => {
            if (a.is_dir !== b.is_dir) return b.is_dir ? 1 : -1;
            return a.name.localeCompare(b.name);
          })
        );
      }
    } catch (_) {/* silent */}
    setLoading(false);
  };

  useEffect(() => {
    if (currentWorkspace) {
      loadRoot(currentWorkspace);
    } else {
      setRootEntries([]);
    }
  }, [currentWorkspace]);

  const handleFileClick = (node: FileNode) => {
    setPreview(node.path);
  };

  if (!visible) return null;

  return (
    <div className="flex flex-col h-full overflow-hidden border-l border-[var(--border)]"
      style={{ width: 220, minWidth: 180, flexShrink: 0 }}>
      {/* header */}
      <div className="flex items-center justify-between px-2 py-1.5 border-b border-[var(--border)] bg-[var(--header-bg)] flex-shrink-0">
        <span className="text-xs font-semibold text-[var(--foreground)] truncate">
          {currentWorkspace ? currentWorkspace.split('/').pop() : 'Files'}
        </span>
        <button
          className="icon-btn flex-shrink-0"
          onClick={() => currentWorkspace && loadRoot(currentWorkspace)}
          title="Refresh"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="23 4 23 10 17 10" />
            <polyline points="1 20 1 14 7 14" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
          </svg>
        </button>
      </div>

      {/* tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {!currentWorkspace ? (
          <div className="text-xs text-[var(--muted)] text-center py-6 italic">Select a workspace</div>
        ) : loading ? (
          <div className="text-xs text-[var(--muted)] text-center py-4">Loading…</div>
        ) : rootEntries.length === 0 ? (
          <div className="text-xs text-[var(--muted)] text-center py-4 italic">Empty directory</div>
        ) : (
          rootEntries.map((node) => (
            <DirTreeNode
              key={node.path}
              node={node}
              indent={0}
              activePath={previewPath}
              onFileClick={handleFileClick}
            />
          ))
        )}
      </div>
    </div>
  );
}

// ── Main file content viewer ──────────────────────────────────────

interface WorkspaceFileMainProps {
  showFileTree: boolean;
  onToggleFileTree: () => void;
}

function WorkspaceFileMain({ showFileTree, onToggleFileTree }: WorkspaceFileMainProps) {
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

  if (!currentWorkspace) {
    return (
      <div className="flex-1 flex items-center justify-center text-[var(--muted)] text-sm italic">
        Click a workspace in the sidebar to get started
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* file header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--border)] bg-[var(--header-bg)] flex-shrink-0">
        <span className="text-sm font-medium truncate flex-1 text-[var(--foreground)]">
          {fileName || <span className="italic text-[var(--muted)]">No file selected</span>}
        </span>
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Edit button — only shown for text files */}
          {previewPath && previewType === 'text' && (
            previewEditing ? (
              <>
                <button
                  className="text-xs px-2 py-1 rounded bg-[var(--blue)] text-white"
                  onClick={handleSave}
                  disabled={saving}
                >
                  {saving ? 'Saving…' : 'Save'}
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
            )
          )}
          {/* Files toggle button */}
          <button
            className={`text-xs px-2 py-1 rounded border ${showFileTree ? 'border-[var(--accent)] text-[var(--accent)]' : 'border-[var(--border2)]'}`}
            onClick={onToggleFileTree}
            title={showFileTree ? 'Hide file tree' : 'Show file tree'}
          >
            Files
          </button>
        </div>
      </div>

      {/* file body */}
      {!previewPath ? (
        <div className="flex-1 flex items-center justify-center text-[var(--muted)] text-sm italic">
          Select a file from the directory tree
        </div>
      ) : (
        <div className="flex-1 overflow-auto p-4">
          {previewLoading ? (
            <div className="flex items-center justify-center py-8">
              <svg className="animate-spin" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
                <path d="M12 2a10 10 0 0 1 10 10" />
              </svg>
            </div>
          ) : previewType === 'image' ? (
            <img src={previewContent || ''} alt={previewPath} className="max-w-full max-h-[70vh] mx-auto" />
          ) : previewType === 'markdown' && !previewEditing ? (
            <div className="prose prose-sm max-w-none text-[var(--foreground)]"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(previewContent || '') }} />
          ) : previewType === 'text' || previewEditing ? (
            previewEditing ? (
              <textarea
                className="w-full h-full min-h-[60vh] bg-[var(--code-bg)] text-[var(--pre-text)] font-mono text-sm p-3 rounded resize-none outline-none border border-[var(--border2)]"
                value={previewContent || ''}
                onChange={(e) => updatePreviewContent(e.target.value)}
                spellCheck={false}
                autoFocus
              />
            ) : (
              <pre className="text-sm font-mono text-[var(--pre-text)] whitespace-pre-wrap break-words bg-[var(--code-bg)] p-3 rounded">
                {previewContent}
              </pre>
            )
          ) : (
            <div className="text-center text-[var(--muted)] py-8 italic">Binary file – cannot preview</div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────

export default function WorkspaceDetail() {
  const [showFileTree, setShowFileTree] = useState(false);

  return (
    <div className="flex h-full overflow-hidden">
      <WorkspaceFileMain
        showFileTree={showFileTree}
        onToggleFileTree={() => setShowFileTree((v) => !v)}
      />
      <WorkspaceDirTree visible={showFileTree} />
    </div>
  );
}
