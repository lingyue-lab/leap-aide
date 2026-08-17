import { create } from 'zustand';
import type { FileNode, DirEntry, Workspace } from '@/types';
import { detectFileType } from '@/components/shared/FileView';

interface WorkspaceState {
  currentDir: string;
  entries: FileNode[];
  gitInfo?: { branch?: string; modified?: number };
  loading: boolean;
  previewPath: string | null;
  previewContent: string | null;
  previewType: 'text' | 'image' | 'markdown' | 'binary' | null;
  previewLoading: boolean;
  previewDirty: boolean;
  previewEditing: boolean;
  workspaces: Workspace[];
  lastWorkspace: string | null;
  currentWorkspace: string | null;
  workspacesTabActive: boolean;
  showFileTree: boolean;

  // Actions
  loadDir: (path: string) => Promise<void>;
  setPreview: (path: string | null) => Promise<void>;
  updatePreviewContent: (content: string) => void;
  setPreviewEditing: (editing: boolean) => void;
  setPreviewDirty: (dirty: boolean) => void;
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
  previewType: null,
  previewLoading: false,
  previewDirty: false,
  previewEditing: false,
  workspaces: [],
  lastWorkspace: null,
  currentWorkspace: null,
  workspacesTabActive: false,
  showFileTree: false,
  showNewWorkspaceForm: false,

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

  setPreview: async (path) => {
    if (!path) {
      set({
        previewPath: null,
        previewContent: null,
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

  savePreview: async () => {
    const { previewPath, previewContent } = get();
    if (!previewPath || previewContent === null) return;

    try {
      const response = await fetch('/api/workspace/file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: previewPath, content: previewContent }),
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to save file');
      set({ previewDirty: false });
    } catch (err) {
      throw err;
    }
  },

  deleteFile: async (path) => {
    const response = await fetch('/api/workspace/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
      credentials: 'include',
    });
    if (!response.ok) throw new Error('Failed to delete file');
    const { previewPath } = get();
    if (previewPath === path) {
      set({ previewPath: null, previewContent: null, previewType: null, previewEditing: false });
    }
  },

  createFileItem: async (path, isDir) => {
    const response = await fetch('/api/workspace/new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, is_dir: isDir }),
      credentials: 'include',
    });
    if (!response.ok) throw new Error('Failed to create');
  },

  renameFileItem: async (src, dst) => {
    const response = await fetch('/api/workspace/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ src, dst }),
      credentials: 'include',
    });
    if (!response.ok) throw new Error('Failed to rename');
    const { previewPath } = get();
    if (previewPath === src) {
      set({ previewPath: dst });
    }
  },

  loadWorkspaces: async () => {
    try {
      const response = await fetch('/api/workspaces', { credentials: 'include' });
      if (!response.ok) throw new Error('Failed to load workspaces');
      const data = await response.json();
      const lastWs: string | null = data.last_workspace || null;
      set({ workspaces: data.workspaces || [], lastWorkspace: lastWs, currentWorkspace: lastWs });
      if (lastWs) {
        try {
          await get().loadDir(lastWs);
          const { entries } = get();
          const firstFile = entries.find((e) => !e.is_dir);
          if (firstFile) {
            get().setPreview(firstFile.path).catch(() => {/* silent */});
          }
        } catch (_) {/* silent */}
      }
    } catch (err) {
      // Fail silently
    }
  },

  selectWorkspace: async (path) => {
    // Update local state immediately; backend call is best-effort
    set({ lastWorkspace: path, currentWorkspace: path, currentDir: path, previewPath: null, previewContent: null, previewType: null });
    try {
      await fetch('/api/workspaces/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
        credentials: 'include',
      });
    } catch (_err) {
      // Fail silently - local state already updated
    }
    // Load directory for selected workspace, then auto-preview first file
    try {
      await get().loadDir(path);
      const { entries } = get();
      const firstFile = entries.find((e) => !e.is_dir);
      if (firstFile) {
        get().setPreview(firstFile.path).catch(() => {/* silent */});
      }
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
