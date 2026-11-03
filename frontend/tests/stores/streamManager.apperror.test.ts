import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as StreamManager from '@/stores/streamManager';
import { useSessionStore } from '@/stores/useSessionStore';
import { useChatStore } from '@/stores/useChatStore';
import { showInfoCard, dismissInfoCard } from '@/components/dialogs/InfoCard';
import { wsManager } from '@/stores/wsManager';

// InfoCard is mocked so we can assert the error card is shown without
// mounting the chat UI; wsManager is mocked so no WebSocket is opened.
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

const SESSION = 'sess-issue38';

async function beginStream(): Promise<void> {
  useSessionStore.setState({ currentSessionId: SESSION, currentSession: null });
  useChatStore.setState({
    messages: [{ role: 'user', content: 'hi', timestamp: 1 } as never],
    busy: true,
  });
  await StreamManager.startSessionStream(SESSION, 'stream-1');
}

describe('streamManager — apperror surfaces an error card (issue #38)', () => {
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

  it('shows the provider error and hint for a quota/billing failure', async () => {
    await beginStream();

    StreamManager.handleReplayEvent(SESSION, 'apperror', {
      message: 'Insufficient Balance: please top up your account',
      type: 'quota_exhausted',
      hint: 'Your provider account is out of credits.',
    });

    expect(showInfoCard).toHaveBeenCalledTimes(1);
    const [, content] = vi.mocked(showInfoCard).mock.calls[0];
    expect(content).toContain('Insufficient Balance');
    expect(content).toContain('out of credits');

    const state = StreamManager.getSessionStreamState(SESSION);
    expect(state).not.toBeNull();
    expect(state!.busy).toBe(false);
    expect(state!.streaming).toBe(false);
    expect(state!.completedAt).not.toBeNull();
    expect(state!.error).toContain('Insufficient Balance');
    expect(wsManager.clearSubscription).toHaveBeenCalledWith(SESSION);
  });

  it('keeps the raw provider message for no_response errors', async () => {
    await beginStream();

    StreamManager.handleReplayEvent(SESSION, 'apperror', {
      message: '402 Payment Required',
      type: 'no_response',
      hint: 'Verify your API key is valid.',
    });

    const [, content] = vi.mocked(showInfoCard).mock.calls[0];
    // The underlying error must not be replaced by a generic sentence (#38).
    expect(content).toContain('402 Payment Required');
    expect(content).toContain('Verify your API key');
  });

  it('falls back to a generic sentence when no message is provided', async () => {
    await beginStream();

    StreamManager.handleReplayEvent(SESSION, 'apperror', {
      message: '',
      type: 'no_response',
    });

    const [, content] = vi.mocked(showInfoCard).mock.calls[0];
    expect(content).toContain('No response received');
  });

  it('shows a card when the stream errors out', async () => {
    await beginStream();

    StreamManager.handleReplayEvent(SESSION, 'error', {});

    const [, content] = vi.mocked(showInfoCard).mock.calls[0];
    expect(content).toContain('Connection lost');
    const state = StreamManager.getSessionStreamState(SESSION);
    expect(state!.busy).toBe(false);
    expect(state!.streaming).toBe(false);
  });
});
