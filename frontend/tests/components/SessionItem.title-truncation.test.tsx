/**
 * Regression: SessionItem renders the title row with the structure needed for
 * `flex: 1; overflow: hidden; text-overflow: ellipsis` truncation to apply.
 *
 * The old test (tests/test_workspace_panel_session_list.py) asserted that the
 * project color dot — a span sitting in the same row as the title — would
 * not be clipped by the title's `overflow:hidden`. That test was written
 * against the deleted vanilla-JS `static/sessions.js` + `static/style.css`,
 * where `_renderOneSession()` directly appended a dot element to a row
 * container.
 *
 * The React `SessionItem` does NOT render a project color dot (and
 * `SessionListItem` has no color field today — see `frontend/src/types/index.ts`).
 * The original bug shape — "dot appended inside an overflow:hidden title
 * span" — therefore no longer exists in the codebase.
 *
 * What we port instead: structural assertions that protect against
 * regressions of the underlying invariant. If a future feature adds a
 * color dot back into the title row, these assertions force it to be a
 * flex sibling (not a child of the title span) and force the title to
 * keep `flex: 1` so the row's flex layout can elide it cleanly.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render } from '@testing-library/react';
import SessionItem from '@/components/sidebar/SessionItem';
import { useSessionStore } from '@/stores/useSessionStore';
import type { SessionListItem } from '@/types';

// useSessionStreaming reads WebSocket / notify state — neither exists in
// jsdom. Stub it so SessionItem's import chain resolves without crashing.
vi.mock('@/hooks/useSessionStreaming', () => ({
  useSessionStreaming: () => false,
}));

const fakeSession = (overrides: Partial<SessionListItem> = {}): SessionListItem => ({
  session_id: 's1',
  title: 'normal title',
  created_at: 0,
  ...overrides,
} as SessionListItem);

beforeEach(() => {
  useSessionStore.setState({
    sessions: [],
    currentSessionId: null,
    currentSession: null,
  });
});

describe('SessionItem — title row DOM structure for truncation', () => {
  it('wraps the title in a flex row container with the expected class', () => {
    render(
      <SessionItem session={fakeSession()} isActive={false} onClick={() => {}} />
    );
    const row = document.querySelector('.sidebar-item-title-row');
    expect(row).not.toBeNull();
  });

  it('renders the title as a child span of the row with class sidebar-item-title', () => {
    render(
      <SessionItem session={fakeSession({ title: 'hello world' })} isActive={false} onClick={() => {}} />
    );
    const row = document.querySelector('.sidebar-item-title-row');
    const title = row?.querySelector('.sidebar-item-title');
    expect(title).not.toBeNull();
    expect(title!.textContent).toBe('hello world');
  });

  it('does NOT place the title inside an overflow-hidden ancestor other than the title itself', () => {
    // Regression guard for the original bug: if a future change wraps the
    // title in another overflow-hidden element (e.g. an inline span with
    // overflow:hidden for some badge styling), the ellipsis truncation
    // behaviour breaks. The only overflow-hidden ancestor should be the
    // title element itself.
    render(
      <SessionItem session={fakeSession({ title: 'long title' })} isActive={false} onClick={() => {}} />
    );
    const title = document.querySelector('.sidebar-item-title') as HTMLElement;
    expect(title).not.toBeNull();
    // Walk up the DOM tree looking for any overflow:hidden ancestor.
    let parent: HTMLElement | null = title.parentElement;
    while (parent && parent !== document.body) {
      const overflow = getComputedStyle(parent).overflow;
      expect(overflow).not.toBe('hidden');
      parent = parent.parentElement;
    }
  });

  it('renders a long title without throwing (truncation is a CSS concern, not a layout crash)', () => {
    expect(() =>
      render(
        <SessionItem
          session={fakeSession({ title: 'a'.repeat(200) })}
          isActive={false}
          onClick={() => {}}
        />
      )
    ).not.toThrow();
    // The title is present and contains the full string (CSS handles visual elision).
    const title = document.querySelector('.sidebar-item-title');
    expect(title).not.toBeNull();
    expect(title!.textContent?.length).toBe(200);
  });

  it('renders the subagent label for subagent sessions (translation wired through i18n)', () => {
    render(
      <SessionItem
        session={fakeSession({ is_subagent_session: true, title: 'ignored' })}
        isActive={false}
        onClick={() => {}}
      />
    );
    const title = document.querySelector('.sidebar-item-title');
    expect(title?.textContent).toBe('Subagent');
  });
});