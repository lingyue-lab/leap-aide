import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as StreamManager from '@/stores/streamManager';
import { useSessionStore } from '@/stores/useSessionStore';
import { useChatStore } from '@/stores/useChatStore';
import { showInfoCard, dismissInfoCard } from '@/components/dialogs/InfoCard';
import { wsManager } from '@/stores/wsManager';

// Same mocks as the apperror harness: InfoCard is mocked so assertions do not
// need the chat UI mounted; wsManager is mocked so no WebSocket is opened.
vi.mock('@/components/dialogs/InfoCard', () => ({
  showInfoCard: vi.fn(),
  dismissInfoCard: vi.fn(),
}));
vi.mock('@/stores/wsManager', () => ({
  wsManager: {
    connectSession: vi.fn(() => () => {}),
    clearSubscription: vi.fn(),
    disconnectSession: vi.fn(),
    disconnect: vi.fn(),
    ensureConnected: vi.fn(),
  },
}));

const SESSION = 'sess-issue21';

async function beginStream(): Promise<void> {
  useSessionStore.setState({ currentSessionId: SESSION, currentSession: null });
  useChatStore.setState({
    messages: [{ role: 'user', content: 'hi', timestamp: 1 } as never],
    busy: true,
  });
  await StreamManager.startSessionStream(SESSION, 'stream-1');
}

/** Attach a `clarify-resolved` listener for the duration of `fn`. */
function onResolved(fn: () => void): ReturnType<typeof vi.fn> {
  const spy = vi.fn();
  window.addEventListener('clarify-resolved', spy);
  try {
    fn();
  } finally {
    window.removeEventListener('clarify-resolved', spy);
  }
  return spy;
}

describe('streamManager — forwards clarify resolution (issue #21)', () => {
  beforeEach(() => {
    vi.mocked(showInfoCard).mockClear();
    vi.mocked(dismissInfoCard).mockClear();
    vi.mocked(wsManager.clearSubscription).mockClear();
  });

  afterEach(() => {
    StreamManager.stopBackgroundStream(SESSION);
    StreamManager.clearSessionStream(SESSION);
    useSessionStore.setState({ currentSessionId: null, currentSession: null });
    useChatStore.setState({ messages: [], busy: false });
  });

  it('clarify_resolved forwards a clarify-resolved window event with session id', async () => {
    await beginStream();

    const spy = onResolved(() => {
      StreamManager.handleReplayEvent(SESSION, 'clarify_resolved', {
        clarify_id: 'c1',
        reason: 'timeout',
      });
    });

    expect(spy).toHaveBeenCalledTimes(1);
    const detail = spy.mock.calls[0][0] as CustomEvent;
    expect(detail.detail.clarify_id).toBe('c1');
    expect(detail.detail.reason).toBe('timeout');
    expect(detail.detail._session_id).toBe(SESSION);
  });

  it('clarify_resolved does not hit the unknown-event warning', async () => {
    await beginStream();

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      onResolved(() => {
        StreamManager.handleReplayEvent(SESSION, 'clarify_resolved', { clarify_id: 'c1' });
      });
      const unknown = warnSpy.mock.calls.filter((args) =>
        String(args[0]).includes('Unknown replay event'),
      );
      expect(unknown).toHaveLength(0);
    } finally {
      warnSpy.mockRestore();
    }
  });

  it('done forwards a silent stream-ended dismissal', async () => {
    await beginStream();
    // The done case fires an async loadSession; stub it so no HTTP request is
    // attempted in the test environment (its failure is caught anyway).
    const loadSpy = vi.spyOn(useSessionStore.getState(), 'loadSession')
      .mockResolvedValue(undefined);

    try {
      const spy = onResolved(() => {
        StreamManager.handleReplayEvent(SESSION, 'done', { usage: null });
      });

      expect(spy).toHaveBeenCalledTimes(1);
      const detail = spy.mock.calls[0][0] as CustomEvent;
      expect(detail.detail.reason).toBe('stream-ended');
      expect(detail.detail._session_id).toBe(SESSION);
      // Existing done behavior must be unchanged.
      expect(wsManager.clearSubscription).toHaveBeenCalledWith(SESSION);
    } finally {
      loadSpy.mockRestore();
    }
  });

  it('cancel forwards a silent stream-ended dismissal', async () => {
    await beginStream();

    const spy = onResolved(() => {
      StreamManager.handleReplayEvent(SESSION, 'cancel', {});
    });

    expect(spy).toHaveBeenCalledTimes(1);
    const detail = spy.mock.calls[0][0] as CustomEvent;
    expect(detail.detail.reason).toBe('stream-ended');
    expect(detail.detail._session_id).toBe(SESSION);
  });
});
