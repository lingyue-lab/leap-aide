/**
 * NavTabs providers-gate behavior:
 *  - Clicking chat/tasks when gate returns 'passed' should call onPanelChange.
 *  - Clicking chat/tasks when gate returns 'navigated' should NOT call
 *    onPanelChange.
 *  - Clicking other tabs (settings, skills, memory, workspaces, terminal)
 *    should NOT call gateProviders at all and should always pass through
 *    to onPanelChange.
 *
 * Note: there is no 'cancelled' outcome — the gate uses an Alert Dialog
 * (M3 Basic) with a single primary action; chat / cron panels are
 * unreachable without a configured key, so a Cancel button would leave
 * the user stuck.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/react';

vi.mock('@/utils/providersGate', () => ({
  gateProviders: vi.fn(),
}));

vi.mock('@/stores/useSkillStore', () => ({
  useSkillStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ clearSelection: () => {}, setSkillsTabActive: () => {}, cancelEditing: () => {} }),
}));

vi.mock('@/stores/useCronStore', () => ({
  useCronStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ setTasksTabActive: () => {} }),
}));

vi.mock('@/stores/useMemoryStore', () => ({
  useMemoryStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ setMemoryTabActive: () => {} }),
}));

vi.mock('@/stores/useWorkspaceStore', () => ({
  useWorkspaceStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ setWorkspacesTabActive: () => {} }),
}));

vi.mock('@/stores/useTerminalStore', () => ({
  useTerminalStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ setTerminalTabActive: () => {} }),
}));

vi.mock('@/stores/useSettingsTabStore', () => ({
  useSettingsTabStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel({ setSettingsTabActive: () => {} }),
}));

vi.mock('@/stores/useSessionStore', () => {
  const fakeState = { sessions: [], currentSessionId: null };
  const useSessionStore = (sel?: (s: typeof fakeState) => unknown) =>
    sel ? sel(fakeState) : fakeState;
  // Attach getState for the non-hook access pattern used in NavTabs.
  (useSessionStore as unknown as { getState: () => typeof fakeState }).getState =
    () => fakeState;
  return { useSessionStore };
});

vi.mock('@/hooks/useStreamNotify', () => ({
  setChatPanelActive: () => {},
}));

vi.mock('@/utils/sessionSeen', () => ({
  markSessionSeen: () => {},
}));

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ logout: vi.fn(), authEnabled: false }),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { gateProviders } = await import('@/utils/providersGate');
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { default: NavTabs } = await import('@/components/sidebar/NavTabs');

const mockedGate = vi.mocked(gateProviders);

beforeEach(() => {
  cleanup();
  mockedGate.mockReset();
});

describe('NavTabs — providers gate', () => {
  // TC-N1
  it('clicking Chat calls onPanelChange when gate returns "passed"', async () => {
    mockedGate.mockResolvedValueOnce('passed');
    const onPanelChange = vi.fn();
    const { getByLabelText } = render(
      <NavTabs activePanel="tasks" onPanelChange={onPanelChange} />,
    );
    fireEvent.click(getByLabelText('tabs.chat_title'));
    // onPanelChange is called synchronously after the async gate resolves.
    await vi.waitFor(() => expect(onPanelChange).toHaveBeenCalledWith('chat'));
    expect(mockedGate).toHaveBeenCalledTimes(1);
  });

  // TC-N3
  it('clicking Tasks does NOT call onPanelChange when gate returns "navigated"', async () => {
    mockedGate.mockResolvedValueOnce('navigated');
    const onPanelChange = vi.fn();
    const { getByLabelText } = render(
      <NavTabs activePanel="chat" onPanelChange={onPanelChange} />,
    );
    fireEvent.click(getByLabelText('tabs.tasks_title'));
    await vi.waitFor(() => expect(mockedGate).toHaveBeenCalledTimes(1));
    expect(onPanelChange).not.toHaveBeenCalled();
  });

  // TC-N4 — non-gated tabs pass through unconditionally.
  it.each([
    ['Settings', 'settings.preferences'],
    ['Skills', 'tabs.skills_title'],
    ['Memory', 'tabs.memory_title'],
    ['Workspaces', 'tabs.workspaces_title'],
    ['Terminal', 'tabs.terminal_title'],
  ])('clicking %s does NOT call gateProviders', async (_label, ariaLabel) => {
    const onPanelChange = vi.fn();
    const { getByLabelText } = render(
      <NavTabs activePanel="chat" onPanelChange={onPanelChange} />,
    );
    fireEvent.click(getByLabelText(ariaLabel));
    await vi.waitFor(() => expect(onPanelChange).toHaveBeenCalled());
    expect(mockedGate).not.toHaveBeenCalled();
  });

  it('treats a thrown gate as fail-open (passed)', async () => {
    mockedGate.mockRejectedValueOnce(new Error('boom'));
    const onPanelChange = vi.fn();
    const { getByLabelText } = render(
      <NavTabs activePanel="tasks" onPanelChange={onPanelChange} />,
    );
    fireEvent.click(getByLabelText('tabs.chat_title'));
    await vi.waitFor(() => expect(onPanelChange).toHaveBeenCalledWith('chat'));
  });
});
