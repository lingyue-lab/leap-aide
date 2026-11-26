import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import * as StreamManager from '@/stores/streamManager';
import { useSessionStore } from '@/stores/useSessionStore';
import { useChatStore } from '@/stores/useChatStore';
import { showInfoCard, dismissInfoCard } from '@/components/dialogs/InfoCard';
import { wsManager } from '@/stores/wsManager';

// InfoCard is mocked so we can assert the failure card without mounting
// the chat UI; wsManager is mocked so no WebSocket is opened.
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

const SESSION = 'sess-done-status';
const RETRY_SUMMARY =
  'API call failed after 3 retries: Non-streaming API call timed out after 90s ' +
  'with no response (threshold: 90s)';

async function beginStream(): Promise<void> {
  useSessionStore.setState({ currentSessionId: SESSION, currentSession: null });
  useChatStore.setState({
    messages: [{ role: 'user', content: 'hi', timestamp: 1 } as never],
    busy: true,
  });
  await StreamManager.startSessionStream(SESSION, 'stream-1');
}

describe('streamManager — done event surfaces subagent failure status', () => {
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

  it('shows a failure card when done carries status=error with a summary', async () => {
    await beginStream();

    StreamManager.handleReplayEvent(SESSION, 'done', {
      status: 'error',
      summary: RETRY_SUMMARY,
    });

    expect(showInfoCard).toHaveBeenCalledTimes(1);
    const [, content] = vi.mocked(showInfoCard).mock.calls[0];
    expect(content).toContain('API call failed after 3 retries');

    const state = StreamManager.getSessionStreamState(SESSION);
    expect(state).not.toBeNull();
    expect(state!.busy).toBe(false);
    expect(state!.streaming).toBe(false);
    expect(state!.error).toContain('API call failed after 3 retries');
    expect(wsManager.clearSubscription).toHaveBeenCalledWith(SESSION);
  });

  it('treats the SDK timeout status as a failure too', async () => {
    await beginStream();

    StreamManager.handleReplayEvent(SESSION, 'done', { status: 'timeout' });

    expect(showInfoCard).toHaveBeenCalledTimes(1);
    const [, content] = vi.mocked(showInfoCard).mock.calls[0];
    expect(content).toContain('Subagent task failed');
    const state = StreamManager.getSessionStreamState(SESSION);
    expect(state!.error).toContain('Subagent task failed');
  });

  it('does not raise a card for a normal completion', async () => {
    await beginStream();

    StreamManager.handleReplayEvent(SESSION, 'done', {
      status: 'completed',
      summary: 'All tests green',
    });

    expect(showInfoCard).not.toHaveBeenCalled();
    const state = StreamManager.getSessionStreamState(SESSION);
    expect(state!.error).toBeNull();
    expect(state!.busy).toBe(false);
    expect(wsManager.clearSubscription).toHaveBeenCalledWith(SESSION);
  });

  it('does not raise a card for a plain done without status (parent sessions)', async () => {
    await beginStream();

    StreamManager.handleReplayEvent(SESSION, 'done', {
      usage: { input_tokens: 10, output_tokens: 5 },
    });

    expect(showInfoCard).not.toHaveBeenCalled();
    const state = StreamManager.getSessionStreamState(SESSION);
    expect(state!.error).toBeNull();
  });
});
