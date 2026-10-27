/**
 * Regression for issue #4 — compose-footer workspace chip in welcome state.
 *
 * Welcome state renders ChatComposer against a client-only placeholder
 * session (`new-local-<uuid>`). Before the fix, picking a workspace POSTed
 * /api/session/workspace with that guessed id, the backend answered 404,
 * and the chip never changed. The menu also offered a meaningless "None"
 * row for the workspace concept.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ChatComposer from '@/components/chat/ChatComposer';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

// Mount-time catalog fetches are irrelevant here; keep them out of the network.
vi.mock('@/services/api', async () => {
  const actual = await vi.importActual<any>('@/services/api');
  return {
    ...actual,
    modelsService: { getModels: vi.fn(() => Promise.resolve({ groups: [] })) },
    personalityService: { getAll: vi.fn(() => Promise.resolve([])) },
  };
});

vi.mock('@/components/common/ApprovalIndicator', () => ({ default: () => null }));
vi.mock('@/components/chat/ContextUsageIndicator', () => ({ default: () => null }));

const PLACEHOLDER_SID = 'new-local-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';

const placeholder = (workspace: string) =>
  ({
    session_id: PLACEHOLDER_SID,
    title: 'Untitled',
    workspace,
    messages: [],
    created_at: 0,
  }) as any;

describe('ChatComposer — workspace chip in welcome state', () => {
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    useWorkspaceStore.setState({
      workspaces: [
        { name: 'Alpha', path: '/ws/alpha', builtin: true },
        { name: 'Beta', path: '/ws/beta' },
      ] as any,
      workspacesLoaded: true,
    });
    const ph = placeholder('/ws/alpha');
    useSessionStore.setState({
      sessions: [],
      currentSessionId: PLACEHOLDER_SID,
      currentSession: ph,
      pendingNewSession: ph,
    });
    fetchSpy = vi.fn(() =>
      Promise.resolve(new Response('{}', { status: 200 })),
    );
    global.fetch = fetchSpy as unknown as typeof fetch;
  });

  it('offers no "None" row for the workspace chip', async () => {
    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);
    fireEvent.click(screen.getByTitle('Alpha'));
    await waitFor(() => expect(document.querySelector('.selection-popover')).not.toBeNull());
    expect(screen.queryByText('None')).toBeNull();
  });

  it('applies the pick locally and never POSTs for a placeholder session', async () => {
    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);
    fireEvent.click(screen.getByTitle('Alpha'));
    await waitFor(() => expect(document.querySelector('.selection-popover')).not.toBeNull());

    fireEvent.click(screen.getByText('Beta'));

    await waitFor(() =>
      expect(useSessionStore.getState().currentSession?.workspace).toBe('/ws/beta'),
    );
    const workspacePosts = fetchSpy.mock.calls.filter((c) =>
      String(c[0]).includes('/api/session/workspace'),
    );
    expect(workspacePosts).toHaveLength(0);
    expect(useSessionStore.getState().pendingNewSession?.workspace).toBe('/ws/beta');
    // The chip reflects the new pick.
    expect(screen.getByTitle('Beta')).toBeInTheDocument();
  });
});
