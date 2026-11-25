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
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, act, cleanup } from '@testing-library/react';
import ClarifyCard, { setClarifyCardCurrentSessionId } from '@/components/dialogs/ClarifyCard';
import { showInfoCard } from '@/components/dialogs/InfoCard';
import i18n from '@/i18n/config';

vi.mock('@/services/api', () => ({
  clarifyService: { respond: vi.fn().mockResolvedValue({ ok: true }) },
}));
vi.mock('@/components/dialogs/InfoCard', () => ({
  showInfoCard: vi.fn(),
  dismissInfoCard: vi.fn(),
}));

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

// ── Resolution + dismissed-memory contract (issue #21) ─────────────────────
// Pins Task 3's `clarify-resolved` listener: the event's detail shape, the
// timeout notice shown exactly once, the silent dismissals, and the bounded
// per-session dismissed-id memory that stops stream replay from resurrecting
// dead cards. All of these fail today — no listener exists yet — except the
// two guard tests (wrong-id, other-session), which pass vacuously and must
// stay green after the fix.

/** Dispatch a `clarify-resolved` window event (resolution contract). */
function dispatchResolved(detail: { _session_id: string; clarify_id?: string; reason?: string }) {
  act(() => {
    window.dispatchEvent(new CustomEvent('clarify-resolved', { detail }));
  });
}

describe('ClarifyCard resolution handling (issue #21)', () => {
  // The Task-1 tests above trigger the timeout dismiss path, which calls the
  // mocked showInfoCard — clear call history so each test counts only its own.
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('clarify-resolved with reason timeout hides a visible card and shows the timeout notice once', () => {
    const sid = 'sess-res-timeout';
    mountCard(sid);
    dispatchClarify(sid, 'cid-res-timeout');

    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();

    dispatchResolved({ _session_id: sid, clarify_id: 'cid-res-timeout', reason: 'timeout' });

    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();
    expect(showInfoCard).toHaveBeenCalledTimes(1);
    expect(showInfoCard).toHaveBeenCalledWith(sid, i18n.t('chat:clarify.timeout'));

    // A duplicate delivery of the same resolved event must not repeat the notice.
    dispatchResolved({ _session_id: sid, clarify_id: 'cid-res-timeout', reason: 'timeout' });
    expect(showInfoCard).toHaveBeenCalledTimes(1);
  });

  it('clarify-resolved without reason hides the card silently', () => {
    const sid = 'sess-res-silent';
    mountCard(sid);
    dispatchClarify(sid, 'cid-res-silent');

    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();

    dispatchResolved({ _session_id: sid, clarify_id: 'cid-res-silent' });

    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();
    expect(showInfoCard).not.toHaveBeenCalled();
  });

  it('clarify-resolved for a different clarify_id leaves the card untouched', () => {
    const sid = 'sess-res-wrong-id';
    mountCard(sid);
    dispatchClarify(sid, 'cid-res-a');

    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();

    dispatchResolved({ _session_id: sid, clarify_id: 'cid-res-b' });

    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();
  });

  it('re-delivery of a dismissed clarify id never re-shows the card', () => {
    const sid = 'sess-res-replay';
    mountCard(sid);
    dispatchClarify(sid, 'cid-res-replay');

    dispatchResolved({ _session_id: sid, clarify_id: 'cid-res-replay' });
    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();

    // Refresh-then-replay: the backend re-sends the now-dead clarification.
    dispatchClarify(sid, 'cid-res-replay');
    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();
  });

  it('a successful respond survives later replay', async () => {
    const sid = 'sess-respond-replay';
    const view = mountCard(sid);
    dispatchClarify(sid, 'cid-respond-replay');

    const firstChoice = view.container.querySelector<HTMLButtonElement>('.bg-task-btns button');
    expect(firstChoice).not.toBeNull();
    await act(async () => {
      firstChoice!.click();
    });

    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();

    dispatchClarify(sid, 'cid-respond-replay');
    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();
  });

  it('dismissed id memory is bounded to 50 per session', () => {
    const sid = 'sess-dismissed-cap';
    mountCard(sid);

    for (let i = 1; i <= 55; i++) {
      dispatchResolved({ _session_id: sid, clarify_id: `d${i}` });
    }

    // d55 is within the 50-id cap → still remembered as dismissed.
    dispatchClarify(sid, 'd55');
    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();

    // d1 is the oldest and was evicted → its clarification may show again.
    dispatchClarify(sid, 'd1');
    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();
  });

  it('stream-ended dismissal is silent', () => {
    const sid = 'sess-stream-ended';
    mountCard(sid);
    dispatchClarify(sid, 'cid-stream-ended');

    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();

    dispatchResolved({ _session_id: sid, reason: 'stream-ended' });

    expect(document.querySelector('.bg-task-card')).not.toBeInTheDocument();
    expect(showInfoCard).not.toHaveBeenCalled();
  });

  it('clarify-resolved for another session does not hide the current card', () => {
    const sid = 'sess-current-x';
    mountCard(sid);
    dispatchClarify(sid, 'cid-current-x');

    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();

    dispatchResolved({ _session_id: 'sess-other-y', clarify_id: 'cid-other-y' });

    expect(document.querySelector('.bg-task-card')).toBeInTheDocument();
  });
});
