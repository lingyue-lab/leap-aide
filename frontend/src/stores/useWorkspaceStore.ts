import { create } from 'zustand';
import type { FileNode, DirEntry, Workspace, WorkspaceInfo } from '@/types';
import { detectFileType } from '@/components/shared/FileView';
import { workspaceService } from '@/services/api';

interface WorkspaceState {
  currentDir: string;
  entries: FileNode[];
  gitInfo?: { branch?: string; modified?: number };
  loading: boolean;
  previewPath: string | null;
  previewContent: string | null;
  /** Last content known to be on disk (loaded or successfully saved).
   *  Cancel restores this so "Cancel" really discards edits. */
  previewOriginalContent: string | null;
  previewType: 'text' | 'image' | 'markdown' | 'binary' | null;
  previewLoading: boolean;
  previewDirty: boolean;
  previewEditing: boolean;
  workspaces: Workspace[];
  workspacesLoaded: boolean;
  /** Panel selection only — pure frontend state (workspace ownership lives
   *  on each session's state.db cwd; there is no global last workspace). */
  currentWorkspace: string | null;
  workspacesTabActive: boolean;
  showFileTree: boolean;
  workspaceInfo: WorkspaceInfo | null;
  workspaceInfoLoading: boolean;
  /** True when a file mutation (save/create/rename/delete) made the cached
   *  overview stats (counts, size, git status) stale. WorkspaceOverview
   *  refreshes on mount / on this flag instead of walking the tree after
   *  every keystroke-level action. */
  workspaceInfoStale: boolean;

  // Actions
  loadDir: (path: string) => Promise<void>;
  loadWorkspaceInfo: (path: string, opts?: { silent?: boolean }) => Promise<void>;
  setPreview: (path: string | null) => Promise<void>;
  updatePreviewContent: (content: string) => void;
  setPreviewEditing: (editing: boolean) => void;
  setPreviewDirty: (dirty: boolean) => void;
  /** Exit edit mode and restore the on-disk content (discard edits). */
  cancelPreviewEditing: () => void;
  savePreview: () => Promise<void>;
  deleteFile: (path: string) => Promise<void>;
  createFileItem: (path: string, isDir: boolean) => Promise<void>;
  renameFileItem: (src: string, dst: string) => Promise<void>;
  loadWorkspaces: () => Promise<void>;
  selectWorkspace: (path: string) => Promise<void>;
  addWorkspace: (path: string, displayName?: string) => Promise<void>;
  removeWorkspace: (path: string) => Promise<void>;
  renameWorkspace: (path: string, newName: string) => Promise<void>;
  setWorkspacesTabActive: (active: boolean) => void;
  setShowFileTree: (show: boolean) => void;
  showNewWorkspaceForm: boolean;
  setShowNewWorkspaceForm: (show: boolean) => void;
}

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  currentDir: '.',
  entries: [],
  gitInfo: undefined,
  loading: false,
  previewPath: null,
  previewContent: null,
  previewOriginalContent: null,
  previewType: null,
  previewLoading: false,
  previewDirty: false,
  previewEditing: false,
  workspaces: [],
  workspacesLoaded: false,
  currentWorkspace: null,
  workspacesTabActive: false,
  showFileTree: false,
  showNewWorkspaceForm: false,
  workspaceInfo: null,
  workspaceInfoLoading: false,
  workspaceInfoStale: false,

  loadDir: async (path) => {
    set({ loading: true });
    try {
      const response = await fetch(
        `/api/workspace/list?path=${encodeURIComponent(path)}`,
        { credentials: 'include' }
      );
      if (!response.ok) throw new Error('Failed to load directory');
      const data: DirEntry = await response.json();
      set({
        currentDir: data.path,
        entries: data.entries.sort((a, b) => {
          if (a.is_dir !== b.is_dir) return b.is_dir ? 1 : -1;
          return a.name.localeCompare(b.name);
        }),
        gitInfo: data.git,
        loading: false,
      });
    } catch (err) {
      set({ loading: false });
      throw err;
    }
  },

  loadWorkspaceInfo: async (path, opts) => {
    // silent: keep the current stats on screen while refreshing — avoids a
    // spinner flash when the summary refreshes after a file mutation.
    const silent = opts?.silent === true;
    set(
      silent
        ? { workspaceInfoStale: false }
        : { workspaceInfoLoading: true, workspaceInfo: null, workspaceInfoStale: false }
    );
    try {
      const response = await fetch(
        `/api/workspace/info?path=${encodeURIComponent(path)}`,
        { credentials: 'include' }
      );
      if (!response.ok) throw new Error('Failed to load workspace info');
      const data: WorkspaceInfo = await response.json();
      set({ workspaceInfo: data, workspaceInfoLoading: false });
    } catch (_err) {
      set({ workspaceInfoLoading: false });
    }
  },

  setPreview: async (path) => {
    if (!path) {
      set({
        previewPath: null,
        previewContent: null,
        previewOriginalContent: null,
        previewType: null,
        previewDirty: false,
        previewEditing: false,
      });
      return;
    }

    set({ previewLoading: true, previewPath: path });
    try {
      const response = await fetch(
        `/api/workspace/file?path=${encodeURIComponent(path)}`,
        { credentials: 'include' }
      );
      if (!response.ok) {
        set({ previewType: 'binary', previewLoading: false });
        return;
      }

      const content = await response.text();

      const previewType = detectFileType(path, content) as WorkspaceState['previewType'];

      set({
        previewContent: content,
        previewOriginalContent: content,
        previewType,
        previewLoading: false,
        previewDirty: false,
      });
    } catch (err) {
      set({
        previewType: 'binary',
        previewLoading: false,
      });
    }
  },

  updatePreviewContent: (content) => set({ previewContent: content, previewDirty: true }),

  setPreviewEditing: (editing) => set({ previewEditing: editing }),

  setPreviewDirty: (dirty) => set({ previewDirty: dirty }),

  cancelPreviewEditing: () => {
    const { previewOriginalContent } = get();
    set({
      previewContent: previewOriginalContent,
      previewDirty: false,
      previewEditing: false,
    });
  },

  savePreview: async () => {
    const { previewPath, previewContent } = get();
    if (!previewPath || previewContent === null) return;

    // workspaceService -> api() throws an Error carrying the backend's
    // {"error": ...} text (e.g. "Permission denied"), so the save alert tells
    // the user what actually failed instead of a generic message (#42).
    await workspaceService.saveFile(previewPath, previewContent);
    // The saved snapshot becomes the new "on disk" baseline for Cancel.
    // If the user kept typing while the request was in flight, their newer
    // edits stay dirty.
    const stillDirty = get().previewContent !== previewContent;
    set({
      previewDirty: stillDirty,
      previewOriginalContent: previewContent,
      workspaceInfoStale: true,
    });
  },

  deleteFile: async (path) => {
    await workspaceService.deleteFile(path);
    const { previewPath } = get();
    // Also clear the preview when an ancestor directory was deleted.
    const previewRemoved =
      previewPath === path || (previewPath?.startsWith(`${path}/`) ?? false);
    if (previewRemoved) {
      set({
        previewPath: null,
        previewContent: null,
        previewOriginalContent: null,
        previewType: null,
        previewEditing: false,
        previewDirty: false,
      });
    }
    set({ workspaceInfoStale: true });
  },

  createFileItem: async (path, isDir) => {
    await workspaceService.createFile(path, isDir);
    set({ workspaceInfoStale: true });
  },

  renameFileItem: async (src, dst) => {
    await workspaceService.moveFile(src, dst);
    const { previewPath } = get();
    if (previewPath === src) {
      set({ previewPath: dst });
    } else if (previewPath?.startsWith(`${src}/`)) {
      // The open file lives inside the renamed directory — follow it.
      set({ previewPath: dst + previewPath.slice(src.length) });
    }
    set({ workspaceInfoStale: true });
  },

  loadWorkspaces: async () => {
    try {
      const response = await fetch('/api/workspaces', { credentials: 'include' });
      if (!response.ok) throw new Error('Failed to load workspaces');
      const data = await response.json();
      const workspaces: Workspace[] = data.workspaces || [];
      // Panel selection initializes to the built-in Default workspace —
      // pure frontend state, no persisted "last workspace".
      const builtin = workspaces.find((w) => w.builtin) || null;
      set({
        workspaces,
        workspacesLoaded: true,
        currentWorkspace: builtin ? builtin.path : null,
      });
      if (builtin) {
        try {
          await Promise.all([
            get().loadDir(builtin.path),
            get().loadWorkspaceInfo(builtin.path),
          ]);
        } catch (_) {/* silent */}
      }
    } catch (err) {
      // Fail silently
    }
  },

  selectWorkspace: async (path) => {
    // Purely local: the panel's "current selection" is frontend state.
    // Workspace ownership belongs to each session (state.db cwd), so
    // there is no global last-workspace to persist on selection.
    set({ currentWorkspace: path, currentDir: path, previewPath: null, previewContent: null, previewType: null, workspaceInfoLoading: true, workspaceInfo: null });
    // Load directory listing and workspace info (no auto-preview)
    try {
      await Promise.all([
        get().loadDir(path),
        get().loadWorkspaceInfo(path),
      ]);
    } catch (_) {/* silent */}
  },

  addWorkspace: async (path: string, displayName?: string) => {
    try {
      const response = await fetch('/api/workspaces/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, display_name: displayName }),
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to add workspace');
      // Reload full workspace list (includes external flags + derived workspaces)
      await get().loadWorkspaces();
    } catch (err) {
      throw err;
    }
  },

  removeWorkspace: async (path: string) => {
    try {
      const response = await fetch('/api/workspaces/remove', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to remove workspace');
      await get().loadWorkspaces();
    } catch (err) {
      throw err;
    }
  },

  renameWorkspace: async (path: string, newName: string) => {
    try {
      const response = await fetch('/api/workspaces/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, name: newName }),
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to rename workspace');
      await get().loadWorkspaces();
    } catch (err) {
      throw err;
    }
  },

  setWorkspacesTabActive: (active) => set({ workspacesTabActive: active }),
  setShowFileTree: (show) => set({ showFileTree: show }),
  setShowNewWorkspaceForm: (show) => set({ showNewWorkspaceForm: show }),
}));
