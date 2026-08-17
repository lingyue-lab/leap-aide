import { useTranslation } from 'react-i18next';
import { useSkillStore } from '@/stores/useSkillStore';
import { useCronStore } from '@/stores/useCronStore';
import { useMemoryStore } from '@/stores/useMemoryStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useTerminalStore } from '@/stores/useTerminalStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { useSessionStore } from '@/stores/useSessionStore';
import { setChatPanelActive } from '@/hooks/useStreamNotify';
import { sessionService } from '@/services/api';
import type { NavPanel } from '@/types';
import { Icon } from '@/components/common/Icons';
import { useAuth } from '@/contexts/AuthContext';

interface NavTabsProps {
  activePanel: NavPanel;
  onPanelChange: (panel: NavPanel) => void;
}

interface TabConfig {
  panel: NavPanel;
  labelKey: string;
  titleKey: string;
  icon: string;
}

export default function NavTabs({ activePanel, onPanelChange }: NavTabsProps) {
  const { t } = useTranslation('common');
  const { logout, authEnabled } = useAuth();
  const clearSelection = useSkillStore((s) => s.clearSelection);
  const setSkillsTabActive = useSkillStore((s) => s.setSkillsTabActive);
  const cancelEditing = useSkillStore((s) => s.cancelEditing);
  const setTasksTabActive = useCronStore((s) => s.setTasksTabActive);
  const setMemoryTabActive = useMemoryStore((s) => s.setMemoryTabActive);
  const setWorkspacesTabActive = useWorkspaceStore((s) => s.setWorkspacesTabActive);
  const setTerminalTabActive = useTerminalStore((s) => s.setTerminalTabActive);
  const setSettingsTabActive = useSettingsTabStore((s) => s.setSettingsTabActive);

  // Derive whether any session has unread messages (for chat tab badge).
  // When last_seen_msg_count is null (never tracked, e.g. unopened CLI sessions),
  // default to message_count so the dot doesn't light permanently.
  const hasSessionUnread = useSessionStore((s) =>
    s.sessions.some((ses) => {
      const mc = ses.message_count ?? 0;
      const lsc = ses.last_seen_msg_count;
      return lsc != null ? mc > lsc : false;
    })
  );

  const tabs: TabConfig[] = [
    {
      panel: 'chat',
      labelKey: 'tabs.chat',
      titleKey: 'tabs.chat_title',
      icon: 'support_agent',
    },
    {
      panel: 'tasks',
      labelKey: 'tabs.tasks',
      titleKey: 'tabs.tasks_title',
      icon: 'calendar_month',
    },
    {
      panel: 'skills',
      labelKey: 'tabs.skills',
      titleKey: 'tabs.skills_title',
      icon: 'layers',
    },
    {
      panel: 'memory',
      labelKey: 'tabs.memory',
      titleKey: 'tabs.memory_title',
      icon: 'psychology',
    },
    {
      panel: 'workspaces',
      labelKey: 'tabs.workspaces',
      titleKey: 'tabs.workspaces_title',
      icon: 'folder_open',
    },
    {
      panel: 'terminal',
      labelKey: 'tabs.terminal',
      titleKey: 'tabs.terminal_title',
      icon: 'terminal_2',
    },
  ];

  const clearAllTabStates = () => {
    setSkillsTabActive(false);
    setTasksTabActive(false);
    setMemoryTabActive(false);
    setWorkspacesTabActive(false);
    setTerminalTabActive(false);
    setSettingsTabActive(false);
  };

  const switchPanel = (panel: NavPanel) => {
    cancelEditing();
    onPanelChange(panel);
    clearAllTabStates();
    // Track whether the chat panel is active so useStreamNotify can
    // correctly decide whether to mark a session as "seen" when its
    // background stream finishes.
    setChatPanelActive(panel === 'chat');
    if (panel === 'skills') {
      setSkillsTabActive(true);
    } else if (panel === 'tasks') {
      setTasksTabActive(true);
    } else if (panel === 'memory') {
      setMemoryTabActive(true);
    } else if (panel === 'workspaces') {
      setWorkspacesTabActive(true);
    } else if (panel === 'terminal') {
      setTerminalTabActive(true);
    } else if (panel === 'settings') {
      setSettingsTabActive(true);
    } else if (panel === 'chat') {
      clearSelection();
      // Mark the current session as seen if it has unread messages
      // (normally handled by SessionItem.handleClick, but when switching
      // back to chat tab the session is already selected — no click needed)
      const currentSessionId = useSessionStore.getState().currentSessionId;
      if (currentSessionId) {
        const currentSession = useSessionStore.getState().sessions.find(
          (s) => s.session_id === currentSessionId
        );
        if (currentSession) {
          const mc = currentSession.message_count ?? 0;
          const lsc = currentSession.last_seen_msg_count ?? 0;
          if (mc > lsc) {
            useSessionStore.getState().updateSessionLastSeen(currentSessionId, mc);
            sessionService.markSessionSeen(currentSessionId, mc).catch(() => {/* ignore */});
          }
        }
      }
    }
  };

  return (
    <div className="nav-tabs">
      {/* Main tabs */}
      <div className="nav-tabs-main">
        {tabs.map((tab) => (
          <button
            key={tab.panel}
            className={`nav-tab ${activePanel === tab.panel ? 'active' : ''}`}
            data-panel={tab.panel}
            data-label={t(tab.labelKey)}
            onClick={() => switchPanel(tab.panel)}
            title={t(tab.titleKey)}
            aria-label={t(tab.titleKey)}
          >
            <Icon name={tab.icon} size={24} fill={activePanel === tab.panel ? 1 : 0} />
            {tab.panel === 'chat' && hasSessionUnread && (
              <span className="nav-tab-dot" />
            )}
          </button>
        ))}
      </div>

      {/* Settings tab pinned at bottom */}
      <div className="nav-tabs-bottom">
        <button
          className={`nav-tab ${activePanel === 'settings' ? 'active' : ''}`}
          data-panel="settings"
          data-label={t('settings.preferences')}
          onClick={() => switchPanel('settings')}
          title={t('settings.preferences')}
          aria-label={t('settings.preferences')}
        >
          <Icon name="settings" size={24} fill={activePanel === 'settings' ? 1 : 0} />
        </button>
        {authEnabled && (
          <button
            className="nav-tab"
            onClick={() => { void logout(); }}
            data-label={t('logout')}
            title={t('logout')}
            aria-label={t('logout')}
          >
            <Icon name="logout" size={24} />
          </button>
        )}
      </div>
    </div>
  );
}
