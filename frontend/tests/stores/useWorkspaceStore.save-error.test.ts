import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

function errorResponse(status: number, error: string) {
  return new Response(JSON.stringify({ error }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('useWorkspaceStore — write errors surface the backend message (#42)', () => {
  beforeEach(() => {
    useWorkspaceStore.setState({
      previewPath: '/tmp/note.txt',
      previewContent: 'edited',
      previewDirty: true,
    });
  });

  it('savePreview rejects with the backend error text, not a generic message', async () => {
    global.fetch = vi.fn().mockResolvedValue(errorResponse(500, 'Permission denied')) as never;

    await expect(useWorkspaceStore.getState().savePreview()).rejects.toThrow('Permission denied');
    expect(useWorkspaceStore.getState().previewDirty).toBe(true);
  });

  it('createFileItem rejects with the backend error text', async () => {
    global.fetch = vi.fn().mockResolvedValue(errorResponse(409, 'Already exists')) as never;

    await expect(
      useWorkspaceStore.getState().createFileItem('/tmp/note.txt', false)
    ).rejects.toThrow('Already exists');
  });

  it('renameFileItem rejects with the backend error text', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue(errorResponse(403, 'Cross-origin request rejected')) as never;

    await expect(
      useWorkspaceStore.getState().renameFileItem('/tmp/a.txt', '/tmp/b.txt')
    ).rejects.toThrow('Cross-origin request rejected');
  });

  it('marks the cached overview stats stale after a successful save', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    ) as never;

    await useWorkspaceStore.getState().savePreview();

    expect(useWorkspaceStore.getState().previewDirty).toBe(false);
    expect(useWorkspaceStore.getState().workspaceInfoStale).toBe(true);
  });

  it('marks the cached overview stats stale after a successful create', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    ) as never;

    await useWorkspaceStore.getState().createFileItem('/tmp/new.txt', false);

    expect(useWorkspaceStore.getState().workspaceInfoStale).toBe(true);
  });
});
