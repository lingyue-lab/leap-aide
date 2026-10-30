/**
 * Regression tests for a bug found in manual testing of issue #5.
 *
 * Repro:
 *   1. Click the Workspaces panel, select "Default" → the main area shows the
 *      workspace detail (`workspacesTabActive` + `currentWorkspace`).
 *   2. Click the Chat tab with no API key configured → the providers gate
 *      dialog appears.
 *   3. Click "Configure providers" → the sidebar switches to
 *      Settings → Model Providers, but the main area keeps showing the
 *      workspace detail.
 *
 * Cause: `NonTerminalContent` (MainContent.tsx:303) checks
 * `workspacesTabActive && currentWorkspace` BEFORE `settingsTabActive`, and
 * `NavTabs.switchPanel()` returns early on a 'navigated' gate outcome — so its
 * `clearAllTabStates()` never runs. `openProvidersSettings()` only set the
 * settings flags, leaving the workspace flag armed.
 *
 * Invariant under test: after navigating to Settings from anywhere, exactly
 * one panel flag is true (settings).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { gateProviders, openProvidersSettings } from '@/utils/providersGate';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';
import { useSidebarPanelStore } from '@/stores/useSidebarPanelStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSkillStore } from '@/stores/useSkillStore';
import { useCronStore } from '@/stores/useCronStore';
import { useMemoryStore } from '@/stores/useMemoryStore';
import { useTerminalStore } from '@/stores/useTerminalStore';
import { providersService } from '@/services/api';

vi.mock('@/services/api', () => ({
  providersService: { getProviders: vi.fn() },
}));

vi.mock('@/components/common/DialogHost', () => ({
  showAlertDialog: vi.fn(),
  showConfirmDialog: vi.fn(),
  showPromptDialog: vi.fn(),
}));

const { showAlertDialog } = await import('@/components/common/DialogHost');
const mockedShowAlert = vi.mocked(showAlertDialog);
const mockedGetProviders = vi.mocked(providersService.getProviders);

const fakeProviders = (rows: Array<{ has_key: boolean }>) =>
  rows.map((r, i) => ({
    id: `p${i}`,
    display_name: `p${i}`,
    has_key: r.has_key,
    configurable: true,
    is_oauth: false,
    key_source: 'none' as const,
    auth_error: null,
    models: [],
  }));

/** Simulate "user is sitting on the Workspaces panel with Default selected". */
function armWorkspacePanel(): void {
  useWorkspaceStore.setState({ workspacesTabActive: true, currentWorkspace: '/workspaces' });
}

function panelFlags() {
  return {
    skills: useSkillStore.getState().skillsTabActive,
    tasks: useCronStore.getState().tasksTabActive,
    memory: useMemoryStore.getState().memoryTabActive,
    workspaces: useWorkspaceStore.getState().workspacesTabActive,
    terminal: useTerminalStore.getState().terminalTabActive,
    settings: useSettingsTabStore.getState().settingsTabActive,
  };
}

describe('Settings navigation from the providers gate (issue #5, manual test)', () => {
  beforeEach(() => {
    useProvidersGateStore.setState({ configured: null, checkedAt: null, lastErrorStatus: null });
    useSidebarPanelStore.setState({ activePanel: 'workspaces', sidebarOpen: true });
    useSettingsTabStore.setState({ settingsTabActive: false, selectedGroup: 'appearance' });
    useWorkspaceStore.setState({ workspacesTabActive: false, currentWorkspace: null });
    useSkillStore.setState({ skillsTabActive: false });
    useCronStore.setState({ tasksTabActive: false });
    useMemoryStore.setState({ memoryTabActive: false });
    useTerminalStore.setState({ terminalTabActive: false });
    mockedShowAlert.mockReset();
    mockedGetProviders.mockReset();
  });

  it('openProvidersSettings() disarms the Workspaces panel', () => {
    armWorkspacePanel();

    openProvidersSettings();

    expect(panelFlags()).toEqual({
      skills: false,
      tasks: false,
      memory: false,
      workspaces: false,
      terminal: false,
      settings: true,
    });
    expect(useWorkspaceStore.getState().currentWorkspace).toBe('/workspaces');
    expect(useSidebarPanelStore.getState().activePanel).toBe('settings');
    expect(useSettingsTabStore.getState().selectedGroup).toBe('providers');
  });

  it('clicking Configure clears every other panel, not just the sidebar', async () => {
    armWorkspacePanel();
    useSkillStore.setState({ skillsTabActive: true });
    useCronStore.setState({ tasksTabActive: true });
    useMemoryStore.setState({ memoryTabActive: true });

    mockedGetProviders.mockResolvedValueOnce({
      providers: fakeProviders([{ has_key: false }]) as never,
      active_provider: null,
    });
    mockedShowAlert.mockResolvedValueOnce(undefined);

    await expect(gateProviders()).resolves.toBe('navigated');

    expect(panelFlags()).toEqual({
      skills: false,
      tasks: false,
      memory: false,
      workspaces: false,
      terminal: false,
      settings: true,
    });
  });
});
