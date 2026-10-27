/**
 * Regression for issue #4: the compose-footer workspace chip must work in
 * welcome state (client-only placeholder session).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useSessionStore } from '@/stores/useSessionStore';

const placeholderSession = (workspace: string) =>
  ({
    session_id: 'new-local-11111111-2222-3333-4444-555555555555',
    title: 'Untitled',
    workspace,
    messages: [],
    created_at: 0,
  }) as any;

describe('useSessionStore.assignWorkspace — welcome-state placeholder', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentSession: null,
      pendingNewSession: null,
    });
  });

  it('keeps the pick local (no POST) when the session is the placeholder', async () => {
    const ph = placeholderSession('/ws/alpha');
    useSessionStore.setState({
      currentSessionId: ph.session_id,
      currentSession: ph,
      pendingNewSession: ph,
    });
    const fetchSpy = vi.fn(() =>
      Promise.resolve(new Response('{}', { status: 200 })),
    );
    global.fetch = fetchSpy as unknown as typeof fetch;

    await useSessionStore.getState().assignWorkspace(ph.session_id, '/ws/beta');

    expect(fetchSpy).not.toHaveBeenCalled();
    const st = useSessionStore.getState();
    expect(st.currentSession?.workspace).toBe('/ws/beta');
    expect(st.pendingNewSession?.workspace).toBe('/ws/beta');
  });

  it('does not throw when the backend has no row for the placeholder', async () => {
    const ph = placeholderSession('/ws/alpha');
    useSessionStore.setState({
      currentSessionId: ph.session_id,
      currentSession: ph,
      pendingNewSession: ph,
    });
    // Mirror the real backend: POST /api/session/workspace → 404 for a
    // session_id it has never seen.
    global.fetch = (() =>
      Promise.resolve(
        new Response(JSON.stringify({ error: 'Session not found' }), {
          status: 404,
        }),
      )) as unknown as typeof fetch;

    await expect(
      useSessionStore.getState().assignWorkspace(ph.session_id, '/ws/beta'),
    ).resolves.toBeUndefined();
    expect(useSessionStore.getState().currentSession?.workspace).toBe('/ws/beta');
  });

  it('still persists to the backend for a real session id', async () => {
    useSessionStore.setState({
      sessions: [{ session_id: 'abc123def456', title: 'T', created_at: 0 } as any],
      currentSessionId: 'abc123def456',
      currentSession: {
        session_id: 'abc123def456',
        title: 'T',
        workspace: '/ws/alpha',
        messages: [],
        created_at: 0,
      } as any,
    });
    const fetchSpy = vi.fn(() =>
      Promise.resolve(new Response('{"ok":true}', { status: 200 })),
    );
    global.fetch = fetchSpy as unknown as typeof fetch;

    await useSessionStore.getState().assignWorkspace('abc123def456', '/ws/beta');

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(String((fetchSpy.mock.calls[0] as any[])[0])).toContain(
      '/api/session/workspace',
    );
    expect(useSessionStore.getState().currentSession?.workspace).toBe('/ws/beta');
  });
});
