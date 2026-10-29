/**
 * NavTabs providers-gate behavior (issue #16 UI invariant: switching panels
 * never awaits the network):
 *  - Clicking chat/tasks switches the panel IMMEDIATELY; the gate runs in
 *    the background afterwards (it only gates SENDING, not switching).
 *  - Clicking other tabs (settings, skills, memory, workspaces, terminal)
 *    should NOT call gateProviders at all.
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

vi.mock('@/stores/useSkillStore', () => {
  const state = { clearSelection: () => {}, setSkillsTabActive: () => {}, cancelEditing: () => {} };
  const useSkillStore = (sel: (s: typeof state) => unknown) => sel(state);
  // Attach getState for the non-hook access pattern (clearPanelTabStates).
  (useSkillStore as unknown as { getState: () => typeof state }).getState = () => state;
  return { useSkillStore };
});

vi.mock('@/stores/useCronStore', () => {
  const state = { setTasksTabActive: () => {} };
  const useCronStore = (sel: (s: typeof state) => unknown) => sel(state);
  // Attach getState for the non-hook access pattern (clearPanelTabStates).
  (useCronStore as unknown as { getState: () => typeof state }).getState = () => state;
  return { useCronStore };
});

vi.mock('@/stores/useMemoryStore', () => {
  const state = { setMemoryTabActive: () => {} };
  const useMemoryStore = (sel: (s: typeof state) => unknown) => sel(state);
  // Attach getState for the non-hook access pattern (clearPanelTabStates).
  (useMemoryStore as unknown as { getState: () => typeof state }).getState = () => state;
  return { useMemoryStore };
});

vi.mock('@/stores/useWorkspaceStore', () => {
  const state = { setWorkspacesTabActive: () => {} };
  const useWorkspaceStore = (sel: (s: typeof state) => unknown) => sel(state);
  // Attach getState for the non-hook access pattern (clearPanelTabStates).
  (useWorkspaceStore as unknown as { getState: () => typeof state }).getState = () => state;
  return { useWorkspaceStore };
});

vi.mock('@/stores/useTerminalStore', () => {
  const state = { setTerminalTabActive: () => {} };
  const useTerminalStore = (sel: (s: typeof state) => unknown) => sel(state);
  // Attach getState for the non-hook access pattern (clearPanelTabStates).
  (useTerminalStore as unknown as { getState: () => typeof state }).getState = () => state;
  return { useTerminalStore };
});

vi.mock('@/stores/useSettingsTabStore', () => {
  const state = { setSettingsTabActive: () => {} };
  const useSettingsTabStore = (sel: (s: typeof state) => unknown) => sel(state);
  // Attach getState for the non-hook access pattern (clearPanelTabStates).
  (useSettingsTabStore as unknown as { getState: () => typeof state }).getState = () => state;
  return { useSettingsTabStore };
});

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
  it('clicking Chat switches immediately and still runs the gate in the background', async () => {
    mockedGate.mockResolvedValueOnce('passed');
    const onPanelChange = vi.fn();
    const { getByLabelText } = render(
      <NavTabs activePanel="tasks" onPanelChange={onPanelChange} />,
    );
    fireEvent.click(getByLabelText('tabs.chat_title'));
    // Issue #16: the switch must NOT wait for the gate promise.
    expect(onPanelChange).toHaveBeenCalledWith('chat');
    await vi.waitFor(() => expect(mockedGate).toHaveBeenCalledTimes(1));
  });

  // TC-N3 (issue #16: 'navigated' no longer blocks the switch — the gate
  // dialog guides the user to Settings but the panel already changed).
  it('clicking Tasks still switches when gate returns "navigated"', async () => {
    mockedGate.mockResolvedValueOnce('navigated');
    const onPanelChange = vi.fn();
    const { getByLabelText } = render(
      <NavTabs activePanel="chat" onPanelChange={onPanelChange} />,
    );
    fireEvent.click(getByLabelText('tabs.tasks_title'));
    expect(onPanelChange).toHaveBeenCalledWith('tasks');
    await vi.waitFor(() => expect(mockedGate).toHaveBeenCalledTimes(1));
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
