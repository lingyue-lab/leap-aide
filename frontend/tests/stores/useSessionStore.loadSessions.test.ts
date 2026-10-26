import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useSessionStore } from '@/stores/useSessionStore';

const listItem = (id: string, title = id) =>
  ({
    session_id: id,
    title,
    created_at: 0,
    pinned: false,
    message_count: 0,
    last_seen_msg_count: null,
  }) as never;

function sessionsResponse(ids: string[]) {
  return new Response(JSON.stringify({ sessions: ids.map((id) => listItem(id)) }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('useSessionStore — loadSessions (async, issue #16)', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentSession: null,
      loading: false,
      error: null,
    });
  });

  it('applies the fetched session list', async () => {
    global.fetch = vi.fn().mockResolvedValue(sessionsResponse(['a', 'b'])) as never;

    await useSessionStore.getState().loadSessions();

    const { sessions, loading, error } = useSessionStore.getState();
    expect(sessions.map((s) => s.session_id)).toEqual(['a', 'b']);
    expect(loading).toBe(false);
    expect(error).toBeNull();
  });

  it('sets error and clears loading on a failed response', async () => {
    global.fetch = vi.fn().mockResolvedValue(new Response('nope', { status: 500 })) as never;

    await useSessionStore.getState().loadSessions();

    const { loading, error } = useSessionStore.getState();
    expect(loading).toBe(false);
    expect(error).toBe('Failed to load sessions');
  });

  it('keeps the active session title from currentSession over the fetched row', async () => {
    useSessionStore.setState({
      currentSessionId: 'a',
      currentSession: { session_id: 'a', title: 'Renamed live', messages: [] } as never,
    });
    global.fetch = vi.fn().mockResolvedValue(sessionsResponse(['a'])) as never;

    await useSessionStore.getState().loadSessions();

    expect(useSessionStore.getState().sessions[0]?.title).toBe('Renamed live');
  });

  it('out-of-order overlapping loads: the newer request wins', async () => {
    // First fetch hangs until we release it; second resolves immediately.
    let releaseFirst!: (r: Response) => void;
    global.fetch = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            releaseFirst = resolve;
          })
      )
      .mockResolvedValueOnce(sessionsResponse(['newer'])) as never;

    const first = useSessionStore.getState().loadSessions(); // seq 1 (slow)
    const second = useSessionStore.getState().loadSessions(); // seq 2 (fast)
    await second;
    expect(useSessionStore.getState().sessions.map((s) => s.session_id)).toEqual([
      'newer',
    ]);

    // The stale first response lands AFTER — it must be discarded.
    releaseFirst(sessionsResponse(['stale']));
    await first;
    expect(useSessionStore.getState().sessions.map((s) => s.session_id)).toEqual([
      'newer',
    ]);
  });
});
