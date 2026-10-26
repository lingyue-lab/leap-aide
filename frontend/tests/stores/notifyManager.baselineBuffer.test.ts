import { describe, it, expect, beforeEach, vi } from 'vitest';
import { handleNotifyEvent } from '@/stores/notifyManager';
import { useSessionStore } from '@/stores/useSessionStore';
import * as StreamManager from '@/stores/streamManager';

// begin_stream triggers StreamManager.startSessionStream (WebSocket) — stub it
// so tests never touch the network; we only care about session-store effects.
vi.mock('@/stores/streamManager', () => ({
  startSessionStream: vi.fn().mockResolvedValue(undefined),
  getActiveSessionIds: vi.fn().mockReturnValue([]),
  getSessionStreamState: vi.fn().mockReturnValue(null),
  clearSessionStream: vi.fn(),
  handleReplayEvent: vi.fn(),
}));

const listItem = (id: string) =>
  ({
    session_id: id,
    title: id,
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

/** Drain microtasks until `predicate` holds (or time out).  The baseline is
 *  an async promise chain, so "fetch issued" / "sessions applied" are never
 *  observable synchronously after handleNotifyEvent. */
async function until(predicate: () => boolean, label: string, rounds = 50) {
  for (let i = 0; i < rounds; i++) {
    if (predicate()) return;
    await Promise.resolve();
  }
  throw new Error(`until(${label}) timed out`);
}

async function flush(rounds = 10) {
  for (let i = 0; i < rounds; i++) await Promise.resolve();
}

describe('notifyManager — baseline buffer (issue #16)', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentSession: null,
      loading: false,
      error: null,
    });
    vi.mocked(StreamManager.getActiveSessionIds).mockReturnValue([]);
    vi.mocked(StreamManager.getSessionStreamState).mockReturnValue(null);
  });

  it('buffers events during the baseline and applies them after, in order', async () => {
    // Baseline fetch hangs until we release it.
    let releaseBaseline!: (r: Response) => void;
    let fetchCalls = 0;
    global.fetch = vi.fn().mockImplementation(() => {
      fetchCalls += 1;
      return new Promise<Response>((resolve) => {
        releaseBaseline = resolve;
      });
    }) as never;

    handleNotifyEvent('subscribe_success', {});
    // Wait until the async baseline chain actually issued the fetch.
    await until(() => fetchCalls === 1, 'fetch #1 issued');

    // Baseline in flight — these two must be buffered, not applied yet.
    handleNotifyEvent('upsert_session', {
      session_id: 'x',
      title: 'from-notify',
      message_count: 3,
    });
    handleNotifyEvent('delete_session', { session_id: 'ghost' });

    expect(useSessionStore.getState().sessions).toHaveLength(0);

    releaseBaseline(sessionsResponse(['base']));
    // Wait for the BUFFERED upsert to land (baseline rows land a few
    // microtasks before the finally replays the buffer).
    await until(
      () => useSessionStore.getState().sessions.some((s) => s.session_id === 'x'),
      'baseline + buffer applied'
    );

    // Baseline applied first, then the buffered upsert inserted its row.
    const sessions = useSessionStore.getState().sessions;
    expect(sessions.map((s) => s.session_id)).toEqual(['x', 'base']);
    expect(sessions[0]?.title).toBe('from-notify');
    expect(sessions[0]?.message_count).toBe(3);
  });

  it('a second subscribe_success during a pending baseline chains and keeps buffering', async () => {
    // Two hanging baseline fetches.
    const releases: Array<(r: Response) => void> = [];
    let fetchCalls = 0;
    global.fetch = vi.fn().mockImplementation(() => {
      fetchCalls += 1;
      return new Promise<Response>((resolve) => {
        releases.push(resolve);
      });
    }) as never;

    handleNotifyEvent('subscribe_success', {}); // baseline #1
    await until(() => fetchCalls === 1, 'fetch #1 issued');
    handleNotifyEvent('upsert_session', { session_id: 'buffered-1', title: 'b1' });
    handleNotifyEvent('subscribe_success', {}); // chains behind #1
    handleNotifyEvent('upsert_session', { session_id: 'buffered-2', title: 'b2' });

    // Nothing may land while either baseline is pending.
    expect(useSessionStore.getState().sessions).toHaveLength(0);

    // Release baseline #1 — its snapshot lands but the buffer must NOT
    // replay yet (baseline #2 still pending).
    releases[0]!(sessionsResponse(['old-snapshot']));
    await until(
      () =>
        useSessionStore.getState().sessions.some((s) => s.session_id === 'old-snapshot'),
      'snapshot #1 applied'
    );
    await flush(); // give a chained replay a chance — it must NOT happen
    expect(useSessionStore.getState().sessions.map((s) => s.session_id)).toEqual([
      'old-snapshot',
    ]);

    // Baseline #2 is now in flight.
    await until(() => fetchCalls === 2, 'fetch #2 issued');
    releases[1]!(sessionsResponse(['new-snapshot']));
    await until(
      () =>
        useSessionStore.getState().sessions.some((s) => s.session_id === 'buffered-2'),
      'buffer replayed after newest baseline'
    );

    // Newest snapshot won, then BOTH buffered events replayed in order —
    // each upsert of a not-yet-known session prepends, so the last replayed
    // row ends up first.
    const sessions = useSessionStore.getState().sessions;
    expect(sessions.map((s) => s.session_id)).toEqual([
      'buffered-2',
      'buffered-1',
      'new-snapshot',
    ]);
    expect(sessions[0]?.title).toBe('b2');
    expect(sessions[1]?.title).toBe('b1');
  });

  it('events arriving after the baseline lands are applied immediately (no buffering)', async () => {
    global.fetch = vi.fn().mockResolvedValue(sessionsResponse(['base'])) as never;

    handleNotifyEvent('subscribe_success', {});
    await until(
      () => useSessionStore.getState().sessions.length > 0,
      'baseline applied'
    );
    // Sessions land a few microtasks before the baseline promise's finally
    // clears _baselinePending — flush so the gate is truly open.
    await flush();
    expect(useSessionStore.getState().sessions.map((s) => s.session_id)).toEqual([
      'base',
    ]);

    // Baseline settled — upsert must apply synchronously.
    handleNotifyEvent('upsert_session', { session_id: 'live', title: 'live' });
    expect(
      useSessionStore.getState().sessions.find((s) => s.session_id === 'live')
    ).toBeTruthy();
  });

  it('buffers begin_stream events so they replay after the baseline', async () => {
    let releaseBaseline!: (r: Response) => void;
    let fetchCalls = 0;
    global.fetch = vi.fn().mockImplementation(() => {
      fetchCalls += 1;
      return new Promise<Response>((resolve) => {
        releaseBaseline = resolve;
      });
    }) as never;

    handleNotifyEvent('subscribe_success', {});
    await until(() => fetchCalls === 1, 'fetch #1 issued');
    handleNotifyEvent('begin_stream', { session_id: 'base', stream_id: 'st-1' });
    expect(StreamManager.startSessionStream).not.toHaveBeenCalled();

    releaseBaseline(sessionsResponse(['base']));
    await until(
      () => useSessionStore.getState().sessions.length > 0,
      'baseline applied'
    );
    await until(
      () => vi.mocked(StreamManager.startSessionStream).mock.calls.length > 0,
      'begin_stream replayed'
    );

    expect(StreamManager.startSessionStream).toHaveBeenCalledWith('base', 'st-1');
    expect(
      useSessionStore.getState().sessions.find((s) => s.session_id === 'base')
        ?.active_stream_id
    ).toBe('st-1');
  });
});
