import React from 'react';
import { useTranslation } from 'react-i18next';
import { useSkillStore } from '@/stores/useSkillStore';
import { useCronStore } from '@/stores/useCronStore';
import { useMemoryStore } from '@/stores/useMemoryStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
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
      panel: 'profiles',
      labelKey: 'tabs.profiles',
      titleKey: 'tabs.profiles_title',
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
          <circle cx="12" cy="7" r="4" />
        </svg>
      ),
    },
    {
      panel: 'todos',
      labelKey: 'tabs.todos',
      titleKey: 'tabs.todos_title',
      icon: (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="3" y="5" width="6" height="6" rx="1" />
          <path d="m3 17 2 2 4-4" />
          <path d="M13 6h8" />
          <path d="M13 12h8" />
          <path d="M13 18h8" />
        </svg>
      ),
    },
  ];

  const switchPanel = (panel: NavPanel) => {
    cancelEditing();
    onPanelChange(panel);
    if (panel === 'skills') {
      setSkillsTabActive(true);
      setTasksTabActive(false);
      setMemoryTabActive(false);
      setWorkspacesTabActive(false);
    } else if (panel === 'tasks') {
      setSkillsTabActive(false);
      setTasksTabActive(true);
      setMemoryTabActive(false);
      setWorkspacesTabActive(false);
    } else if (panel === 'memory') {
      setSkillsTabActive(false);
      setTasksTabActive(false);
      setMemoryTabActive(true);
      setWorkspacesTabActive(false);
    } else if (panel === 'workspaces') {
      setSkillsTabActive(false);
      setTasksTabActive(false);
      setMemoryTabActive(false);
      setWorkspacesTabActive(true);
    } else {
      setSkillsTabActive(false);
      setTasksTabActive(false);
      setMemoryTabActive(false);
      setWorkspacesTabActive(false);
      if (panel === 'chat') {
        clearSelection();
      }
    }
  };

  return (
    <div className="nav-tabs">
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
  );
}
