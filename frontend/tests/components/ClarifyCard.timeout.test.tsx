/**
 * ClarifyCard auto-dismiss (issue #21).
 *
 * After the clarify timeout the card must disappear on its own. The card's
 * dismiss timer is anchored to when the clarification was RECEIVED
 * (stored.receivedAt), so a re-delivered `clarify-required` event with the
 * SAME clarify_id — which happens whenever the WebSocket reconnects and the
 * backend replays the stream history — must NOT restart the countdown.
 *
 * Test 1 proves the timer fires at all. Test 2 is the regression: without
 * dedupe-by-id, every replay resets receivedAt and the card never expires.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, act, cleanup } from '@testing-library/react';
import ClarifyCard, { setClarifyCardCurrentSessionId } from '@/components/dialogs/ClarifyCard';

const TIMEOUT_MS = 600_000; // default clarify timeout (600s)

function dispatchClarify(sessionId: string, clarifyId: string) {
  act(() => {
    window.dispatchEvent(
      new CustomEvent('clarify-required', {
        detail: {
          _session_id: sessionId,
          clarify_id: clarifyId,
          kind: 'clarify',
          question: 'Dark or light?',
          choices_offered: ['Dark', 'Light'],
          requested_at: Date.now() / 1000,
        },
      }),
    );
  });
}

function mountCard(sessionId: string) {
  const view = render(<ClarifyCard />);
  act(() => {
    setClarifyCardCurrentSessionId(sessionId);
  });
  return view;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('ClarifyCard auto-dismiss on timeout', () => {
  it('hides the card once the clarify timeout elapses', () => {
    vi.useFakeTimers();
    const sid = 'sess-timeout-1';
    mountCard(sid);
    dispatchClarify(sid, 'cid-timeout-1');

    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(TIMEOUT_MS);
    });

    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();
  });

  it('does NOT restart the countdown when the same clarify event is re-delivered (stream replay)', () => {
    vi.useFakeTimers();
    const sid = 'sess-replay-1';
    mountCard(sid);
    dispatchClarify(sid, 'cid-replay-1');

    // Halfway through the wait the WS reconnects and the backend replays
    // stream history — the same clarify event arrives again.
    act(() => {
      vi.advanceTimersByTime(TIMEOUT_MS / 2);
    });
    dispatchClarify(sid, 'cid-replay-1');

    // Original deadline: 600s after the FIRST delivery.
    act(() => {
      vi.advanceTimersByTime(TIMEOUT_MS / 2);
    });

    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();
  });

  it('keeps a genuinely NEW clarify request on its own fresh countdown', () => {
    vi.useFakeTimers();
    const sid = 'sess-new-2';
    mountCard(sid);
    dispatchClarify(sid, 'cid-new-a');

    act(() => {
      vi.advanceTimersByTime(TIMEOUT_MS / 2);
    });
    // A different clarify_id is a new question — it may have its own window.
    dispatchClarify(sid, 'cid-new-b');

    act(() => {
      vi.advanceTimersByTime(TIMEOUT_MS / 2);
    });
    // Not yet expired for the new question (only halfway).
    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(TIMEOUT_MS / 2);
    });
    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();
  });
});
