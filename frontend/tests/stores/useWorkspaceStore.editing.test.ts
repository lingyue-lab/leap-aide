import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

function okResponse(body: string) {
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/plain' },
  });
}

function okJson(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('useWorkspaceStore — edit mode save/cancel semantics', () => {
  beforeEach(() => {
    useWorkspaceStore.setState({
      previewPath: '/tmp/note.txt',
      previewContent: 'original',
      previewOriginalContent: 'original',
      previewType: 'text',
      previewEditing: true,
      previewDirty: false,
    });
  });

  it('setPreview records the loaded content as the Cancel baseline', async () => {
    global.fetch = vi.fn().mockResolvedValue(okResponse('on disk')) as never;

    await useWorkspaceStore.getState().setPreview('/tmp/other.txt');

    const state = useWorkspaceStore.getState();
    expect(state.previewContent).toBe('on disk');
    expect(state.previewOriginalContent).toBe('on disk');
  });

  it('cancelPreviewEditing discards edits and restores the on-disk content', () => {
    useWorkspaceStore.getState().updatePreviewContent('edited in memory');
    expect(useWorkspaceStore.getState().previewDirty).toBe(true);

    useWorkspaceStore.getState().cancelPreviewEditing();

    const state = useWorkspaceStore.getState();
    expect(state.previewContent).toBe('original');
    expect(state.previewDirty).toBe(false);
    expect(state.previewEditing).toBe(false);
  });

  it('savePreview makes the saved content the new Cancel baseline', async () => {
    global.fetch = vi.fn().mockResolvedValue(okJson({ ok: true })) as never;
    useWorkspaceStore.getState().updatePreviewContent('edited and saved');

    await useWorkspaceStore.getState().savePreview();

    const state = useWorkspaceStore.getState();
    expect(state.previewDirty).toBe(false);
    expect(state.previewOriginalContent).toBe('edited and saved');

    // Cancelling after a save must keep the saved text, not the pre-save text.
    useWorkspaceStore.getState().updatePreviewContent('unsaved again');
    useWorkspaceStore.getState().cancelPreviewEditing();
    expect(useWorkspaceStore.getState().previewContent).toBe('edited and saved');
  });

  it('deleteFile clears the preview baseline when the previewed file is removed', async () => {
    global.fetch = vi.fn().mockResolvedValue(okJson({ ok: true })) as never;

    await useWorkspaceStore.getState().deleteFile('/tmp/note.txt');

    const state = useWorkspaceStore.getState();
    expect(state.previewPath).toBeNull();
    expect(state.previewContent).toBeNull();
    expect(state.previewOriginalContent).toBeNull();
    expect(state.previewEditing).toBe(false);
  });

  it('renameFileItem follows an open file inside a renamed directory', async () => {
    useWorkspaceStore.setState({ previewPath: '/tmp/docs/note.txt' });
    global.fetch = vi.fn().mockResolvedValue(okJson({ ok: true })) as never;

    await useWorkspaceStore.getState().renameFileItem('/tmp/docs', '/tmp/documentation');

    expect(useWorkspaceStore.getState().previewPath).toBe('/tmp/documentation/note.txt');
  });

  it('deleteFile clears the preview when an ancestor directory is removed', async () => {
    useWorkspaceStore.setState({ previewPath: '/tmp/docs/note.txt' });
    global.fetch = vi.fn().mockResolvedValue(okJson({ ok: true })) as never;

    await useWorkspaceStore.getState().deleteFile('/tmp/docs');

    expect(useWorkspaceStore.getState().previewPath).toBeNull();
    expect(useWorkspaceStore.getState().previewDirty).toBe(false);
  });
});
