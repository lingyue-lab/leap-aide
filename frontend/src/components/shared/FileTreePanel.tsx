import { useState, useRef, useCallback, useEffect, useMemo, createContext, useContext } from 'react';
import { Tree } from 'react-arborist';
import type { NodeRendererProps, RenameHandler } from 'react-arborist';
import ContextMenu, { type ContextMenuEntry } from './ContextMenu';
import {
  EditIcon,
  TrashMultiIcon,
  RenameIcon,
  NewFileIcon,
  NewFolderIcon,
  FolderOpenIcon,
  FolderClosedIcon,
  Icon,
} from '@/components/common/Icons';

// ── Types ────────────────────────────────────────────────────────

export interface FileTreeNode {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
  children?: FileTreeNode[];
}

/** Internal node type for react-arborist */
interface ArboristNode {
  id: string;
  name: string;
  path: string;
  isDir: boolean;
  children?: ArboristNode[];
  loading?: boolean;
  /** True when this is a placeholder being created (not yet saved) */
  isCreating?: boolean;
}

export interface FileTreePanelProps {
  visible: boolean;
  defaultWidth?: number;
  minWidth?: number;
  maxWidth?: number;
  storageKey?: string;
  tree?: FileTreeNode[];
  rootPath?: string;
  loader?: (path: string) => Promise<FileTreeNode[]>;
  loadKey?: string | null;
  defaultExpanded?: boolean;
  activePath?: string | null;
  onFileSelect?: (path: string) => void;
  title?: string;
  showRefresh?: boolean;
  onRefresh?: () => void;
  onClose?: () => void;
  emptyMessage?: string;
  // File operations
  onCreate?: (parentPath: string, name: string, isDir: boolean) => Promise<void>;
  onRename?: (oldPath: string, newPath: string, parentPath: string) => Promise<void>;
  onDelete?: (path: string, isDir: boolean) => Promise<void>;
  onEdit?: (path: string) => void;
}

// ── Context for passing data to NodeRenderer ──────────────────────

interface TreeContextValue {
  activePath: string | null;
  onFileSelect: ((path: string) => void) | undefined;
  onNodeContextMenu: (e: React.MouseEvent, nodeData: ArboristNode) => void;
  onRemoveCreating: (id: string, submittedName?: string) => void;
}

const TreeContext = createContext<TreeContextValue>({
  activePath: null,
  onFileSelect: undefined,
  onNodeContextMenu: () => {},
  onRemoveCreating: () => {},
});

// ── Helpers ──────────────────────────────────────────────────────

function convertNode(n: FileTreeNode): ArboristNode {
  return {
    id: n.path,
    name: n.name,
    path: n.path,
    isDir: n.is_dir,
    children: n.children?.map(convertNode),
  };
}

function findNodeById(nodes: ArboristNode[], id: string): ArboristNode | null {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (n.children) {
      const found = findNodeById(n.children, id);
      if (found) return found;
    }
  }
  return null;
}

function parentPath(nodePath: string): string {
  const i = nodePath.lastIndexOf('/');
  return i > 0 ? nodePath.substring(0, i) : '';
}

/** Immutably add a child node to the parent with the given id. */
function addNodeChild(nodes: ArboristNode[], parentId: string, child: ArboristNode): ArboristNode[] {
  return nodes.map((n) => {
    if (n.id === parentId) {
      return { ...n, children: [...(n.children || []), child] };
    }
    if (n.children) {
      return { ...n, children: addNodeChild(n.children, parentId, child) };
    }
    return n;
  });
}

/** Immutably remove a node by id from the tree. */
function removeNodeById(nodes: ArboristNode[], id: string): ArboristNode[] {
  return nodes
    .filter((n) => n.id !== id)
    .map((n) => {
      if (n.children) {
        return { ...n, children: removeNodeById(n.children, id) };
      }
      return n;
    });
}

// ── Resize hook ──────────────────────────────────────────────────

function usePanelResize(
  storageKey: string | undefined,
  defaultWidth: number,
  minWidth: number,
  maxWidth: number,
) {
  const [panelWidth, setPanelWidth] = useState(() => {
    if (storageKey) {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const parsed = parseInt(saved, 10);
        if (!isNaN(parsed) && parsed >= minWidth && parsed <= maxWidth) return parsed;
      }
    }
    return defaultWidth;
  });
  const [isDragging, setIsDragging] = useState(false);
  const isResizing = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);
  const panelWidthRef = useRef(panelWidth);
  useEffect(() => { panelWidthRef.current = panelWidth; }, [panelWidth]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    startX.current = e.clientX;
    startWidth.current = panelWidthRef.current;
    setIsDragging(true);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isResizing.current) return;
    const zoom = parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue('--app-zoom').trim()
    ) || 1;
    const delta = (e.clientX - startX.current) / zoom;
    const newWidth = Math.min(maxWidth, Math.max(minWidth, startWidth.current - delta));
    setPanelWidth(newWidth);
  }, [maxWidth, minWidth]);

  const handleMouseUp = useCallback(() => {
    if (isResizing.current) {
      isResizing.current = false;
      setIsDragging(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      if (storageKey) localStorage.setItem(storageKey, panelWidthRef.current.toString());
    }
  }, [storageKey]);

  useEffect(() => {
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [handleMouseMove, handleMouseUp]);

  return { panelWidth, isDragging, handleMouseDown };
}

// ── Spinner icon (inline, tiny) ──────────────────────────────────

function Spinner() {
  return (
    <Icon name="progress_activity" size={11} className="animate-spin flex-shrink-0" style={{ opacity: 0.6 }} />
  );
}

// ── Node renderer (reads context for activePath / callbacks) ─────

function NodeRenderer({ node, style }: NodeRendererProps<ArboristNode>) {
  const { activePath, onFileSelect, onNodeContextMenu, onRemoveCreating } = useContext(TreeContext);
  const { isDir, name, loading, isCreating } = node.data;
  const isActive = activePath === node.data.path;
  const isOpen = node.isOpen;
  const isEditing = node.state.isEditing;
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-focus input when entering create or edit mode
  useEffect(() => {
    if (isCreating || isEditing) {
      const timer = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(timer);
    }
  }, [isCreating, isEditing]);

  // ── Creating placeholder ───────────────────────────────────
  if (isCreating) {
    const submitCreate = (val: string) => {
      const name = val.trim();
      if (name) onRemoveCreating(node.data.id, name);
      else onRemoveCreating(node.data.id);
    };
    const handleCreateKeyDown = (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') submitCreate((e.target as HTMLInputElement).value);
      if (e.key === 'Escape') onRemoveCreating(node.data.id);
    };

    return (
      <div style={style} className="file-tree-panel-node">
        <span className="w-[11px] flex-shrink-0" />
        {isDir
          ? <FolderClosedIcon size={18} className="flex-shrink-0 text-[var(--gold)]" />
          : (
            <Icon name="description" size={14} className="flex-shrink-0 text-[var(--muted)]" />
          )
        }
        <input
          ref={inputRef}
          className="sidebar-item-title-input"
          defaultValue=""
          placeholder={isDir ? 'folder name' : 'file name'}
          onKeyDown={handleCreateKeyDown}
          onBlur={(e) => submitCreate((e.target as HTMLInputElement).value)}
        />
      </div>
    );
  }

  // ── Arborist rename editing ────────────────────────────────
  // When arborist puts the node in editing mode (via tree.edit()),
  // we show an input. node.submit(value) commits; node.reset() cancels.
  if (isEditing) {
    const handleEditKeyDown = (e: React.KeyboardEvent) => {
      if (e.key === 'Enter') {
        node.submit((e.target as HTMLInputElement).value);
      }
      if (e.key === 'Escape') {
        node.reset();
      }
    };

    return (
      <div style={style} className="file-tree-panel-node">
        <span className="w-[11px] flex-shrink-0" />
        {isDir
          ? <FolderClosedIcon size={18} className="flex-shrink-0 text-[var(--gold)]" />
          : (
            <Icon name="description" size={14} className="flex-shrink-0 text-[var(--muted)]" />
          )
        }
        <input
          ref={inputRef}
          className="sidebar-item-title-input"
          defaultValue={name}
          onKeyDown={handleEditKeyDown}
          onBlur={() => node.reset()}
        />
      </div>
    );
  }

  // ── Normal display ─────────────────────────────────────────
  const handleClick = () => {
    if (isDir) {
      node.toggle();
    } else {
      onFileSelect?.(node.data.path);
    }
  };

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    onNodeContextMenu(e, node.data);
  };

  return (
    <div
      style={style}
      className={`file-tree-panel-node${isActive ? ' active' : ''}`}
      onClick={handleClick}
      onContextMenu={handleContextMenu}
    >
      {isDir ? (
        loading ? <Spinner /> : (
          <Icon name="keyboard_arrow_down" size={11}
            className={`flex-shrink-0 transition-transform ${isOpen ? '' : '-rotate-90'}`}
            style={{ opacity: 0.6 }} />
        )
      ) : (
        <span className="w-[11px] flex-shrink-0" />
      )}
      {isDir ? (
        isOpen
          ? <FolderOpenIcon size={18} className="flex-shrink-0 text-[var(--gold)]" />
          : <FolderClosedIcon size={18} className="flex-shrink-0 text-[var(--gold)]" />
      ) : (
        <Icon name="description" size={14}
          className={`flex-shrink-0 ${isActive ? 'text-[var(--blue)]' : 'text-[var(--muted)]'}`} />
      )}
      <span className={`truncate flex-1${isActive ? ' font-medium' : ''}`}>{name}</span>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────────

export default function FileTreePanel({
  visible,
  defaultWidth = 280,
  minWidth = 180,
  maxWidth = 500,
  storageKey,
  tree,
  rootPath,
  loader,
  loadKey,
  defaultExpanded,
  activePath,
  onFileSelect,
  title = 'Files',
  showRefresh = true,
  onRefresh,
  onClose,
  emptyMessage,
  onCreate,
  onRename,
  onDelete,
  onEdit,
}: FileTreePanelProps) {
  const { panelWidth, isDragging, handleMouseDown } = usePanelResize(
    storageKey, defaultWidth, minWidth, maxWidth,
  );

  // ── Data state ──
  const [nodes, setNodes] = useState<ArboristNode[]>([]);
  const [rootLoading, setRootLoading] = useState(false);
  const [rootError, setRootError] = useState('');
  const treeRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const isEager = tree !== undefined;
  const isLazy = !isEager && !!rootPath && !!loader;

  const loadRoot = useCallback(async () => {
    if (!isLazy || !rootPath || !loader) return;
    setRootLoading(true);
    setRootError('');
    try {
      const entries = await loader(rootPath);
      setNodes(entries.map(convertNode));
    } catch (err) {
      setRootError(err instanceof Error ? err.message : 'Failed to load');
    } finally {
      setRootLoading(false);
    }
  }, [isLazy, rootPath, loader]);

  useEffect(() => {
    if (isEager && tree) setNodes(tree.map(convertNode));
  }, [isEager, tree]);

  useEffect(() => {
    if (visible) loadRoot();
  }, [visible, loadKey, loadRoot]);

  // ── Context menu ──
  const [ctxMenu, setCtxMenu] = useState<{
    x: number; y: number; items: ContextMenuEntry[];
  } | null>(null);
  const closeCtxMenu = useCallback(() => setCtxMenu(null), []);

  const buildNodeMenu = useCallback(
    (nodeData: ArboristNode): ContextMenuEntry[] => {
      const items: ContextMenuEntry[] = [];
      if (nodeData.isDir) {
        if (onCreate) {
          items.push(
            {
              label: 'New File',
              icon: <NewFileIcon size={16} />,
              onClick: () => insertCreating(nodeData.id, false),
            },
            {
              label: 'New Folder',
              icon: <NewFolderIcon size={16} />,
              onClick: () => insertCreating(nodeData.id, true),
            }
          );
        }
        items.push({ separator: true });
        if (onRename) {
          items.push({
            label: 'Rename',
            icon: <RenameIcon size={16} />,
            onClick: () => treeRef.current?.edit(nodeData.id),
          });
        }
        if (onDelete) {
          items.push({
            label: 'Delete',
            icon: <TrashMultiIcon size={16} />,
            onClick: () => {
              if (confirm(`Delete "${nodeData.name}"?`)) onDelete(nodeData.path, true);
            },
            danger: true,
          });
        }
      } else {
        if (onEdit) {
          items.push({
            label: 'Edit',
            icon: <EditIcon size={16} />,
            onClick: () => onEdit(nodeData.path),
          });
        }
        if (onRename) {
          items.push({
            label: 'Rename',
            icon: <RenameIcon size={16} />,
            onClick: () => treeRef.current?.edit(nodeData.id),
          });
        }
        if (onDelete) {
          items.push({ separator: true });
          items.push({
            label: 'Delete',
            icon: <TrashMultiIcon size={16} />,
            onClick: () => {
              if (confirm(`Delete "${nodeData.name}"?`)) onDelete(nodeData.path, false);
            },
            danger: true,
          });
        }
      }
      return items;
    },
    [nodes, onCreate, onRename, onDelete, onEdit]
  );

  const handleNodeContextMenu = useCallback(
    (e: React.MouseEvent, nodeData: ArboristNode) => {
      setCtxMenu({ x: e.clientX, y: e.clientY, items: buildNodeMenu(nodeData) });
    },
    [buildNodeMenu]
  );

  // Blank-area context menu (root level New File / New Folder)
  const handleTreeContextMenu = useCallback(
    (e: React.MouseEvent) => {
      const row = (e.target as HTMLElement).closest('[role="treeitem"]');
      if (row) return;
      if (!onCreate) return;
      e.preventDefault();
      setCtxMenu({
        x: e.clientX,
        y: e.clientY,
        items: [
          {
            label: 'New File',
            icon: <NewFileIcon size={16} />,
            onClick: () => insertCreating(null, false),
          },
          {
            label: 'New Folder',
            icon: <NewFolderIcon size={16} />,
            onClick: () => insertCreating(null, true),
          },
        ],
      });
    },
    [onCreate]
  );

  // ── Arborist callbacks ──

  /** Remove a creating placeholder, optionally submitting it first. */
  const handleRemoveCreating = useCallback(
    async (id: string, submittedName?: string) => {
      // Find the placeholder node to get parent path and isDir
      const placeholder = findNodeById(nodes, id);
      const parentP = placeholder?.path || '';
      const isDir = placeholder?.isDir ?? false;
      // Remove placeholder from wherever it is
      setNodes((prev) => removeNodeById(prev, id));
      // Submit to backend if name was entered
      if (submittedName && onCreate) {
        try {
          await onCreate(parentP, submittedName, isDir);
          loadRoot();
        } catch (_) {}
      }
    },
    [nodes, onCreate, loadRoot]
  );

  /** Insert a creating placeholder at root or under a parent directory */
  const insertCreating = useCallback(
    (parentId: string | null, isDir: boolean) => {
      const parentNode = parentId ? findNodeById(nodes, parentId) : null;
      const parentP = parentNode?.path || rootPath || '';
      const placeholder: ArboristNode = {
        id: `__creating__${Date.now()}`,
        name: '',
        path: parentP,
        isDir,
        isCreating: true,
      };
      if (parentId && parentNode) {
        // Insert as child of the target directory
        setNodes((prev) => {
          const withChild = addNodeChild(prev, parentId, placeholder);
          return withChild;
        });
      } else {
        setNodes((prev) => [...prev, placeholder]);
      }
    },
    [nodes, rootPath]
  );

  const handleRename: RenameHandler<ArboristNode> = useCallback(
    async ({ name, node: nodeApi }) => {
      if (!onRename) return;
      const nodeData = nodeApi.data;
      const p = parentPath(nodeData.path);
      const newPath = p ? `${p}/${name}` : name;
      if (newPath === nodeData.path) return;
      try {
        await onRename(nodeData.path, newPath, p);
      } catch (_) {}
    },
    [onRename]
  );

  const handleToggle = useCallback(
    async (nodeId: string) => {
      if (!isLazy || !loader) return;
      const node = findNodeById(nodes, nodeId);
      if (!node || !node.isDir || node.children) return;
      node.loading = true;
      setNodes([...nodes]);
      try {
        const entries = await loader(node.path);
        node.children = entries.map(convertNode);
      } catch (_) {}
      node.loading = false;
      setNodes([...nodes]);
    },
    [isLazy, loader, nodes]
  );

  const handleRefresh = () => {
    if (onRefresh) onRefresh();
    else loadRoot();
  };

  // ── Context value for NodeRenderer ──
  const treeContextValue = useMemo<TreeContextValue>(
    () => ({
      activePath: activePath ?? null,
      onFileSelect,
      onNodeContextMenu: handleNodeContextMenu,
      onRemoveCreating: handleRemoveCreating,
    }),
    [activePath, onFileSelect, handleNodeContextMenu, handleRemoveCreating]
  );

  if (!visible) return null;

  const isLoading = isLazy && rootLoading;
  const nodeDefaultExpanded = defaultExpanded ?? isEager;
  const containerHeight = containerRef.current?.clientHeight || 400;

  return (
    <>
      {isDragging && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 9999, cursor: 'col-resize' }} />
      )}

      <div className="file-tree-panel" style={{ width: panelWidth, minWidth, flexShrink: 0 }}>
        <div className="resize-handle" style={{ left: 0, right: 'auto' }} onMouseDown={handleMouseDown} />

        {/* Header */}
        <div className="sidebar-header" style={{ borderBottom: '1px solid var(--border)' }}>
          <span className="sidebar-header-title">{title}</span>
          <div className="sidebar-header-actions">
            {onCreate && (
              <>
                <button className="icon-btn" onClick={() => insertCreating(null, false)} title="New File">
                  <NewFileIcon />
                </button>
                <button className="icon-btn" onClick={() => insertCreating(null, true)} title="New Folder">
                  <NewFolderIcon />
                </button>
              </>
            )}
            {showRefresh && (
              <button className="icon-btn" onClick={handleRefresh} title="Refresh">
                <Icon name="refresh" size={18} />
              </button>
            )}
            {onClose && (
              <button className="icon-btn" onClick={onClose} title="Close">
                <Icon name="close" size={18} />
              </button>
            )}
          </div>
        </div>

        {/* Tree body */}
        <div ref={containerRef} className="file-tree-panel-tree-container" onContextMenu={handleTreeContextMenu}>
          {isLoading ? (
            <div className="flex items-center justify-center py-8 text-[var(--muted)]"><Spinner /></div>
          ) : rootError ? (
            <div className="text-sm text-[var(--accent)] text-center py-4">{rootError}</div>
          ) : nodes.length === 0 ? (
            <div className="text-base text-[var(--muted)] text-center py-8 italic"
              style={{ fontWeight: 400, letterSpacing: '0.25px' }}>
              {emptyMessage || 'No files'}
            </div>
          ) : (
            <TreeContext.Provider value={treeContextValue}>
              <Tree<ArboristNode>
                ref={treeRef}
                data={nodes}
                height={containerHeight}
                width={panelWidth - 1}
                rowHeight={32}
                indent={16}
                openByDefault={nodeDefaultExpanded}
                onToggle={handleToggle}
                onRename={handleRename}
                disableDrag
                disableDrop
              >
                {NodeRenderer}
              </Tree>
            </TreeContext.Provider>
          )}
        </div>
      </div>

      {ctxMenu && <ContextMenu x={ctxMenu.x} y={ctxMenu.y} items={ctxMenu.items} onClose={closeCtxMenu} />}
    </>
  );
}
