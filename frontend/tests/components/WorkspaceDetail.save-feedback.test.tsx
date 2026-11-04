import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import WorkspaceDetail from '@/components/workspace/WorkspaceDetail';
import Snackbar from '@/components/common/Snackbar';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

vi.mock('@/components/shared/FileTreePanel', () => ({ default: () => null }));

const WS = '/tmp/ws';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function seedEditingPreview() {
  useWorkspaceStore.setState({
    currentWorkspace: WS,
    previewPath: `${WS}/note.txt`,
    previewContent: 'hello',
    previewOriginalContent: 'hello',
    previewType: 'text',
    previewLoading: false,
    previewEditing: true,
    previewDirty: false,
    showFileTree: false,
  });
}

/** WorkspaceDetail plus the globally mounted M3 snackbar (see App.tsx). */
function renderPanel() {
  return render(
    <>
      <WorkspaceDetail />
      <Snackbar />
    </>
  );
}

describe('Workspace panel — save confirmation uses the M3 snackbar', () => {
  beforeEach(() => seedEditingPreview());

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a Saved snackbar after a successful save and leaves the button as Save', async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ ok: true })) as never;

    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Saved');
    // The button is not repurposed as the confirmation surface.
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });

  it('does not show a snackbar when the save fails', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue(jsonResponse({ error: 'Permission denied' }, 500)) as never;
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(alertSpy).toHaveBeenCalledWith('Failed to save: Permission denied')
    );
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
