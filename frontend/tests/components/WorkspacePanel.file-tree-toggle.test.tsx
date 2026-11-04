import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import WorkspaceOverview from '@/components/workspace/WorkspaceOverview';
import WorkspaceDetail from '@/components/workspace/WorkspaceDetail';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

// The sidebar itself is covered by its own header Close button; here we only
// assert the duplicate "open/close" toggle in the main topbar disappears
// while the sidebar is visible.
vi.mock('@/components/shared/FileTreePanel', () => ({ default: () => null }));

const WS = '/tmp/ws';

function seedOverview(showFileTree: boolean) {
  useWorkspaceStore.setState({
    currentWorkspace: WS,
    workspaceInfo: {
      path: WS,
      created: null,
      total_size: 0,
      file_count: 0,
      dir_count: 0,
    },
    workspaceInfoLoading: false,
    workspaceInfoStale: false,
    previewPath: null,
    previewContent: null,
    previewType: null,
    previewEditing: false,
    showFileTree,
  });
}

function seedFileView(showFileTree: boolean) {
  useWorkspaceStore.setState({
    currentWorkspace: WS,
    previewPath: `${WS}/note.txt`,
    previewContent: 'hello',
    previewOriginalContent: 'hello',
    previewType: 'text',
    previewLoading: false,
    previewEditing: false,
    showFileTree,
  });
}

describe('Workspace panel — single close affordance for the file tree', () => {
  beforeEach(() => seedOverview(false));

  it('shows the folder button in the overview when the sidebar is closed', () => {
    render(<WorkspaceOverview />);
    expect(screen.getByTitle('Show file tree')).toBeInTheDocument();
  });

  it('hides the overview folder button while the sidebar is open', () => {
    seedOverview(true);
    render(<WorkspaceOverview />);
    expect(screen.queryByTitle('Show file tree')).not.toBeInTheDocument();
  });

  it('shows the folder button in the file view when the sidebar is closed', () => {
    seedFileView(false);
    render(<WorkspaceDetail />);
    expect(screen.getByTitle('Show files')).toBeInTheDocument();
  });

  it('hides the file-view folder button while the sidebar is open', () => {
    seedFileView(true);
    render(<WorkspaceDetail />);
    expect(screen.queryByTitle('Show files')).not.toBeInTheDocument();
  });
});
