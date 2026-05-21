import React from 'react';
import { useTranslation } from 'react-i18next';
import { useSkillStore } from '@/stores/useSkillStore';
import { useCronStore } from '@/stores/useCronStore';
import { useMemoryStore } from '@/stores/useMemoryStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useTerminalStore } from '@/stores/useTerminalStore';
import { useVncStore } from '@/stores/useVncStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import type { NavPanel } from '@/types';

interface NavTabsProps {
  activePanel: NavPanel;
  onPanelChange: (panel: NavPanel) => void;
}

interface TabConfig {
  panel: NavPanel;
  labelKey: string;
  titleKey: string;
  icon: React.ReactNode;
}

export default function NavTabs({ activePanel, onPanelChange }: NavTabsProps) {
  const { t } = useTranslation('common');
  const clearSelection = useSkillStore((s) => s.clearSelection);
  const setSkillsTabActive = useSkillStore((s) => s.setSkillsTabActive);
  const cancelEditing = useSkillStore((s) => s.cancelEditing);
  const setTasksTabActive = useCronStore((s) => s.setTasksTabActive);
  const setMemoryTabActive = useMemoryStore((s) => s.setMemoryTabActive);
  const setWorkspacesTabActive = useWorkspaceStore((s) => s.setWorkspacesTabActive);
  const setTerminalTabActive = useTerminalStore((s) => s.setTerminalTabActive);
  const setVncTabActive = useVncStore((s) => s.setVncTabActive);
  const setSettingsTabActive = useSettingsTabStore((s) => s.setSettingsTabActive);

  const tabs: TabConfig[] = [
    {
      panel: 'chat',
      labelKey: 'tabs.chat',
      titleKey: 'tabs.chat_title',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      ),
    },
    {
      panel: 'tasks',
      labelKey: 'tabs.tasks',
      titleKey: 'tabs.tasks_title',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="3" y="4" width="18" height="18" rx="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>
      ),
    },
    {
      panel: 'skills',
      labelKey: 'tabs.skills',
      titleKey: 'tabs.skills_title',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 2L2 7l10 5 10-5-10-5z" />
          <path d="M2 17l10 5 10-5" />
          <path d="M2 12l10 5 10-5" />
        </svg>
      ),
    },
    {
      panel: 'memory',
      labelKey: 'tabs.memory',
      titleKey: 'tabs.memory_title',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 2a7 7 0 0 1 7 7c0 2.5-1.3 4.7-3.2 6H8.2C6.3 13.7 5 11.5 5 9a7 7 0 0 1 7-7z" />
          <line x1="9" y1="17" x2="15" y2="17" />
          <line x1="10" y1="20" x2="14" y2="20" />
        </svg>
      ),
    },
    {
      panel: 'workspaces',
      labelKey: 'tabs.workspaces',
      titleKey: 'tabs.workspaces_title',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
      ),
    },
    {
      panel: 'terminal',
      labelKey: 'tabs.terminal',
      titleKey: 'tabs.terminal_title',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="4 17 10 11 4 5" />
          <line x1="12" y1="19" x2="20" y2="19" />
        </svg>
      ),
    },
    {
      panel: 'vnc',
      labelKey: 'tabs.vnc',
      titleKey: 'tabs.vnc_title',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
          <line x1="8" y1="21" x2="16" y2="21" />
          <line x1="12" y1="17" x2="12" y2="21" />
        </svg>
      ),
    },
  ];

  const clearAllTabStates = () => {
    setSkillsTabActive(false);
    setTasksTabActive(false);
    setMemoryTabActive(false);
    setWorkspacesTabActive(false);
    setTerminalTabActive(false);
    setVncTabActive(false);
    setSettingsTabActive(false);
  };

  const switchPanel = (panel: NavPanel) => {
    cancelEditing();
    onPanelChange(panel);
    clearAllTabStates();
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
    } else if (panel === 'vnc') {
      setVncTabActive(true);
    } else if (panel === 'settings') {
      setSettingsTabActive(true);
    } else if (panel === 'chat') {
      clearSelection();
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
            {tab.icon}
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
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>
    </div>
  );
}
