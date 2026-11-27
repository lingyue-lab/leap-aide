import { describe, it, expect, beforeEach, vi } from 'vitest';
import { handleNotifyEvent } from '@/stores/notifyManager';
import { useSessionStore } from '@/stores/useSessionStore';
import * as StreamManager from '@/stores/streamManager';

// begin_stream triggers StreamManager.startSessionStream (WebSocket) — stub it
// so tests never touch the network; we only care about session-store effects.
// (Same harness as notifyManager.baselineBuffer.test.ts.)
vi.mock('@/stores/streamManager', () => ({
  startSessionStream: vi.fn().mockResolvedValue(undefined),
  getActiveSessionIds: vi.fn().mockReturnValue([]),
  getSessionStreamState: vi.fn().mockReturnValue(null),
  clearSessionStream: vi.fn(),
  handleReplayEvent: vi.fn(),
}));

/** Seed one session that already has delegate children (1 child, none running). */
function seedSession() {
  useSessionStore.setState({
    sessions: [
      {
        session_id: 'parent-1',
        title: 'parent-1',
        created_at: 0,
        pinned: false,
        message_count: 0,
        last_seen_msg_count: null,
        _has_delegate_children: true,
        _delegate_children_count: 1,
        _delegate_running_count: 0,
      } as never,
    ],
    delegateChildrenVersion: 0,
  });
}

const version = () => useSessionStore.getState().delegateChildrenVersion;

describe('notifyManager — delegateChanged detects _delegate_running_count', () => {
  beforeEach(() => {
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentSession: null,
      loading: false,
      error: null,
      delegateChildrenVersion: 0,
    });
    vi.mocked(StreamManager.getActiveSessionIds).mockReturnValue([]);
    vi.mocked(StreamManager.getSessionStreamState).mockReturnValue(null);
  });

  it('bumps delegateChildrenVersion when only _delegate_running_count changes', () => {
    seedSession();
    const before = version();

    handleNotifyEvent('upsert_session', {
      session_id: 'parent-1',
      _has_delegate_children: true,
      _delegate_children_count: 1,
      _delegate_running_count: 1,
    });

    expect(version()).toBe(before + 1);
  });

  it('does not bump delegateChildrenVersion when all three delegate fields are unchanged', () => {
    seedSession();
    const before = version();

    handleNotifyEvent('upsert_session', {
      session_id: 'parent-1',
      title: 'parent-1 (renamed)',
      _has_delegate_children: true,
      _delegate_children_count: 1,
      _delegate_running_count: 0,
    });

    expect(version()).toBe(before);
    // Sanity: the upsert itself still applied (event processed, not skipped).
    expect(
      useSessionStore.getState().sessions.find((s) => s.session_id === 'parent-1')?.title
    ).toBe('parent-1 (renamed)');
  });

  it('regression: bumps delegateChildrenVersion when _delegate_children_count changes', () => {
    seedSession();
    const before = version();

    handleNotifyEvent('upsert_session', {
      session_id: 'parent-1',
      _has_delegate_children: true,
      _delegate_children_count: 2,
      _delegate_running_count: 0,
    });

    expect(version()).toBe(before + 1);
  });
});
