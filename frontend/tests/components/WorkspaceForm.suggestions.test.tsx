/**
 * Regression for issue #22 — the add-workspace path field must show a
 * directory autocomplete menu (an M3 dropdown anchored under the input) and
 * clicking a suggestion must complete the Workspace Path. The menu stays
 * closed when the backend has nothing to suggest.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

describe('WorkspaceForm — directory autocomplete menu', () => {
  it('opens a listbox under the path input and completes the path on click', async () => {
    mockSuggest({
      '/workspaces/': ['/workspaces/alpha', '/workspaces/beta'],
      '/workspaces/alpha/': ['/workspaces/alpha/src'],
    });

    render(<WorkspaceForm onSuccess={() => {}} onCancel={() => {}} />);
    const input = screen.getByPlaceholderText(
      '/home/user/my-project',
    ) as HTMLInputElement;
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
});
