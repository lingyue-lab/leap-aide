/**
 * Regression for issue #22 — the add-workspace path field must show a
 * directory autocomplete menu (an M3 dropdown anchored under the input) and
 * clicking a suggestion must complete the Workspace Path. The menu stays
 * closed when the backend has nothing to suggest.
 *
 * The later cases pin the interaction rules the first version of the menu
 * got wrong: scrolling the list must not close it, a stale response must
 * not overwrite a newer one, and Tab must not trap the focus.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import WorkspaceForm from '@/components/workspace/WorkspaceForm';

function mockSuggest(byPrefix: Record<string, string[]>) {
  const fn = vi.fn((url: RequestInfo | URL) => {
    const u = String(url);
    for (const [p, s] of Object.entries(byPrefix)) {
      if (u.includes(`prefix=${encodeURIComponent(p)}`)) {
        return Promise.resolve(
          new Response(JSON.stringify({ suggestions: s }), { status: 200 }),
        );
      }
    }
    return Promise.resolve(
      new Response(JSON.stringify({ suggestions: [] }), { status: 200 }),
    );
  });
  global.fetch = fn as unknown as typeof fetch;
  return fn;
}

/** A fetch stub whose responses are resolved by the test, one at a time. */
function mockManualSuggest() {
  const pending: Array<(res: Response) => void> = [];
  global.fetch = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        pending.push(resolve);
      }),
  ) as unknown as typeof fetch;
  return {
    pending,
    resolve(index: number, suggestions: string[], status = 200) {
      pending[index](
        new Response(JSON.stringify({ suggestions }), { status }),
      );
    },
  };
}

function renderForm() {
  render(<WorkspaceForm onSuccess={() => {}} onCancel={() => {}} />);
  return screen.getByPlaceholderText(
    '/home/user/my-project',
  ) as HTMLInputElement;
}

/** Wait for the debounced suggest request for *prefix* to have been sent. */
async function waitForRequest(prefix: string) {
  await waitFor(
    () =>
      expect(
        (global.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some(
          (c) => String(c[0]).includes(`prefix=${encodeURIComponent(prefix)}`),
        ),
      ).toBe(true),
    { timeout: 3000 },
  );
}

describe('WorkspaceForm — directory autocomplete menu', () => {
  it('opens a listbox under the path input and completes the path on click', async () => {
    mockSuggest({
      '/workspaces/': ['/workspaces/alpha', '/workspaces/beta'],
      '/workspaces/alpha/': ['/workspaces/alpha/src'],
    });

    const input = renderForm();
    fireEvent.change(input, { target: { value: '/workspaces/' } });

    await waitFor(
      () =>
        expect(
          screen.getByRole('listbox', { name: 'Directory suggestions' }),
        ).toBeInTheDocument(),
      { timeout: 3000 },
    );
    expect(screen.getByText('/workspaces/alpha')).toBeInTheDocument();
    expect(screen.getByText('/workspaces/beta')).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByText('/workspaces/alpha'));
    await waitFor(() => expect(input.value).toBe('/workspaces/alpha/'), {
      timeout: 3000,
    });
  });

  it('stays closed when the backend returns no matches', async () => {
    const fetchSpy = mockSuggest({ '/nope/': [] });

    render(<WorkspaceForm onSuccess={() => {}} onCancel={() => {}} />);
    const input = screen.getByPlaceholderText('/home/user/my-project');
    fireEvent.change(input, { target: { value: '/nope/' } });

    await waitFor(
      () =>
        expect(
          fetchSpy.mock.calls.some((c) =>
            String(c[0]).includes('prefix=%2Fnope%2F'),
          ),
        ).toBe(true),
      { timeout: 3000 },
    );
    expect(screen.queryByRole('listbox')).toBeNull();
  });

  it('keeps the menu open while the suggestion list itself is scrolled', async () => {
    // The list is capped at 280px, so a long list must be scrollable — and
    // scrolling it must not close the menu.
    mockSuggest({
      '/workspaces/': [
        '/workspaces/one',
        '/workspaces/two',
        '/workspaces/three',
        '/workspaces/four',
      ],
    });

    const input = renderForm();
    fireEvent.change(input, { target: { value: '/workspaces/' } });

    await waitFor(
      () => expect(screen.getByRole('listbox')).toBeInTheDocument(),
      { timeout: 3000 },
    );

    fireEvent.scroll(screen.getByRole('listbox'), { target: { scrollTop: 50 } });
    expect(screen.queryByRole('listbox')).toBeInTheDocument();
  });

  it('ignores a stale response that arrives after a newer one', async () => {
    const api = mockManualSuggest();

    const input = renderForm();
    fireEvent.change(input, { target: { value: '/a' } });
    await waitForRequest('/a');
    fireEvent.change(input, { target: { value: '/ab' } });
    await waitForRequest('/ab');

    // The newer request answers first, the stale one second.
    api.resolve(1, ['/ab/new']);
    await waitFor(() => expect(screen.getByText('/ab/new')).toBeInTheDocument());
    api.resolve(0, ['/a/stale']);

    // Let the stale response land before asserting it was dropped.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.queryByText('/a/stale')).toBeNull();
    expect(screen.getByText('/ab/new')).toBeInTheDocument();
  });

  it('closes the menu when the suggest request fails', async () => {
    const api = mockManualSuggest();

    const input = renderForm();
    fireEvent.change(input, { target: { value: '/workspaces/' } });
    await waitForRequest('/workspaces/');
    api.resolve(0, ['/workspaces/alpha']);
    await waitFor(() => expect(screen.getByRole('listbox')).toBeInTheDocument());

    fireEvent.change(input, { target: { value: '/workspaces/b' } });
    await waitForRequest('/workspaces/b');
    api.resolve(1, [], 500);

    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull());
  });

  it('does not reopen the menu when a late response arrives after clearing', async () => {
    const api = mockManualSuggest();

    const input = renderForm();
    fireEvent.change(input, { target: { value: '/a' } });
    await waitForRequest('/a');

    fireEvent.change(input, { target: { value: '' } });
    api.resolve(0, ['/a/stale']);

    // Let the late response land and the component re-render before
    // asserting — a bare waitFor() would pass before it ever arrives.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.queryByText('/a/stale')).toBeNull();
  });

  it('lets Tab move the focus out when no suggestion is highlighted', async () => {
    mockSuggest({
      '/workspaces/': ['/workspaces/alpha', '/workspaces/beta'],
    });

    const input = renderForm();
    fireEvent.change(input, { target: { value: '/workspaces/' } });
    await waitFor(
      () => expect(screen.getByRole('listbox')).toBeInTheDocument(),
      { timeout: 3000 },
    );

    // fireEvent returns false when the handler called preventDefault().
    expect(fireEvent.keyDown(input, { key: 'Tab' })).toBe(true);
  });

  it('accepts the only suggestion with Tab', async () => {
    mockSuggest({ '/workspaces/': ['/workspaces/alpha'] });

    const input = renderForm();
    fireEvent.change(input, { target: { value: '/workspaces/' } });
    await waitFor(
      () => expect(screen.getByRole('listbox')).toBeInTheDocument(),
      { timeout: 3000 },
    );

    expect(fireEvent.keyDown(input, { key: 'Tab' })).toBe(false);
    await waitFor(() => expect(input.value).toBe('/workspaces/alpha/'), {
      timeout: 3000,
    });
  });
});
