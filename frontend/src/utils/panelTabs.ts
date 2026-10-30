import { useCronStore } from '@/stores/useCronStore';
import { useMemoryStore } from '@/stores/useMemoryStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { useSkillStore } from '@/stores/useSkillStore';
import { useTerminalStore } from '@/stores/useTerminalStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

/**
 * Reset every non-chat panel flag.
 *
 * `MainContent`'s `NonTerminalContent` renders whichever non-chat view has its
 * flag set, and it checks workspaces / skills / memory / tasks BEFORE settings
 * — `workspacesTabActive && currentWorkspace` wins over `settingsTabActive`.
 * Any code that navigates to Settings from outside `NavTabs.switchPanel()`
 * must therefore clear the other flags first, otherwise the sidebar shows
 * Settings while the main area keeps rendering the previous panel
 * (issue #5, found in manual testing: Workspaces → Chat → "Configure
 * providers" left the workspace detail on screen).
 */
export function clearPanelTabStates(): void {
  useSkillStore.getState().setSkillsTabActive(false);
  useCronStore.getState().setTasksTabActive(false);
  useMemoryStore.getState().setMemoryTabActive(false);
  useWorkspaceStore.getState().setWorkspacesTabActive(false);
  useTerminalStore.getState().setTerminalTabActive(false);
  useSettingsTabStore.getState().setSettingsTabActive(false);
}
