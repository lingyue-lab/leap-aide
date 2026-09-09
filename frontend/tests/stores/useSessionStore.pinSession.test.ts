import { describe, it, expect, beforeEach } from 'vitest';
import { useSessionStore } from '@/stores/useSessionStore';

// Helper: build a Session-like object with just the fields the store touches.
// We use `as any` because the full Session type has many optional fields and we
// only need the ones relevant to the mirror behavior under test.
const fakeSession = (overrides: Record<string, unknown> = {}) =>
  ({
    session_id: 's1',
    title: 'T',
    messages: [],
    created_at: 0,
    ...overrides,
  }) as any;

const fakeListItem = (overrides: Record<string, unknown> = {}) =>
  ({
    session_id: 's1',
    title: 'T',
    created_at: 0,
    pinned: false,
    ...overrides,
  }) as any;

describe('useSessionStore — currentSession mirror', () => {
  beforeEach(() => {
    // Reset the store between tests so each one starts from a known state.
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      currentSession: null,
    });
    // Stub fetch so pinSession can resolve without hitting the network.
    global.fetch = (() =>
      Promise.resolve(new Response('{}', { status: 200 }))) as typeof fetch;
  });

  describe('pinSession', () => {
    it('mirrors pinned=true into currentSession when ids match', async () => {
      useSessionStore.setState({
        sessions: [fakeListItem({ session_id: 's1', pinned: false })],
        currentSessionId: 's1',
        currentSession: fakeSession({ session_id: 's1', pinned: false }),
      });

      await useSessionStore.getState().pinSession('s1', true);

      const { currentSession, sessions } = useSessionStore.getState();
      expect(currentSession?.pinned).toBe(true);
      expect(sessions.find((s) => s.session_id === 's1')?.pinned).toBe(true);
    });

    it('mirrors pinned=false into currentSession when ids match', async () => {
      useSessionStore.setState({
        sessions: [fakeListItem({ session_id: 's1', pinned: true })],
        currentSessionId: 's1',
        currentSession: fakeSession({ session_id: 's1', pinned: true }),
      });

      await useSessionStore.getState().pinSession('s1', false);

      expect(useSessionStore.getState().currentSession?.pinned).toBe(false);
    });

    it('does not touch currentSession when id does not match', async () => {
      const cur = fakeSession({ session_id: 's2', pinned: false });
      useSessionStore.setState({
        sessions: [fakeListItem({ session_id: 's1', pinned: false })],
        currentSessionId: 's2',
        currentSession: cur,
      });

      await useSessionStore.getState().pinSession('s1', true);

      // currentSession reference must be preserved when its id doesn't match.
      expect(useSessionStore.getState().currentSession).toBe(cur);
    });
  });

  describe('updateSessionListTitle', () => {
    it('mirrors title into currentSession when ids match', () => {
      useSessionStore.setState({
        sessions: [fakeListItem({ session_id: 's1', title: 'old' })],
        currentSessionId: 's1',
        currentSession: fakeSession({ session_id: 's1', title: 'old' }),
      });

      useSessionStore.getState().updateSessionListTitle('s1', 'new');

      expect(useSessionStore.getState().currentSession?.title).toBe('new');
    });

    it('does not touch currentSession when id does not match', () => {
      const cur = fakeSession({ session_id: 's2', title: 'untouched' });
      useSessionStore.setState({
        sessions: [fakeListItem({ session_id: 's1', title: 'old' })],
        currentSessionId: 's2',
        currentSession: cur,
      });

      useSessionStore.getState().updateSessionListTitle('s1', 'new');

      expect(useSessionStore.getState().currentSession).toBe(cur);
    });
  });
});
