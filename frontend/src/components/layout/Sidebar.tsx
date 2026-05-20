import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import NavTabs from '@/components/sidebar/NavTabs';
import SessionList from '@/components/sidebar/SessionList';
import TasksPanel from '@/components/sidebar/TasksPanel';
import SkillsPanel from '@/components/sidebar/SkillsPanel';
import MemoryPanel from '@/components/sidebar/MemoryPanel';
import WorkspacesPanel from '@/components/sidebar/WorkspacesPanel';
import TerminalPanel from '@/components/sidebar/TerminalPanel';
import VncPanel from '@/components/vnc/VncPanel';
import type { NavPanel } from '@/types';

interface SidebarProps {
  width?: number;
  onResizeStart?: (e: React.MouseEvent) => void;
  onOpenSettings?: () => void;
}

export default function Sidebar({ width = 300, onResizeStart, onOpenSettings }: SidebarProps) {
  const { t } = useTranslation('common');
  const [activePanel, setActivePanel] = useState<NavPanel>('chat');
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const closeSidebar = () => setSidebarOpen(false);

  const handleOpenSettings = () => {
    if (onOpenSettings) {
      onOpenSettings();
    }
  };

  return (
    <>
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-50 md:hidden"
          onClick={closeSidebar}
        />
      )}
      
      <aside 
        className={`sidebar ${sidebarOpen ? 'open' : ''}`}
        style={{ width: `${width}px`, minWidth: `${width}px` }}
      >
        <NavTabs activePanel={activePanel} onPanelChange={setActivePanel} />
        
        <div className="sidebar-content">
        <div className={`panel-view ${activePanel === 'chat' ? 'active' : ''}`} id="panelChat">
            <SessionList />
          </div>
          
          <div className={`panel-view ${activePanel === 'tasks' ? 'active' : ''}`} id="panelTasks">
            <TasksPanel />
          </div>
          
          <div className={`panel-view ${activePanel === 'skills' ? 'active' : ''}`} id="panelSkills">
            <SkillsPanel />
          </div>
          
          <div className={`panel-view ${activePanel === 'memory' ? 'active' : ''}`} id="panelMemory">
            <MemoryPanel />
          </div>
          
          <div className={`panel-view ${activePanel === 'workspaces' ? 'active' : ''}`} id="panelWorkspaces">
            <WorkspacesPanel />
          </div>

          <div className={`panel-view ${activePanel === 'terminal' ? 'active' : ''}`} id="panelTerminal">
            <TerminalPanel />
          </div>

          <div className={`panel-view ${activePanel === 'vnc' ? 'active' : ''}`} id="panelVnc">
            <VncPanel />
          </div>
          
          <div className="sidebar-bottom">
            <button
              className="hermes-launch-btn"
              onClick={handleOpenSettings}
              title="Open Hermes control center"
            >
              <span className="hermes-launch-icon" aria-hidden="true">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
                  <defs>
                    <linearGradient id="hermes-gold-sidebar" x1="0%" y1="0%" x2="0%" y2="100%">
                      <stop offset="0%" style={{ stopColor: '#F5C542', stopOpacity: 1 }} />
                      <stop offset="100%" style={{ stopColor: '#D4961C', stopOpacity: 1 }} />
                    </linearGradient>
                  </defs>
                  <rect x="30" y="10" width="4" height="46" rx="2" fill="url(#hermes-gold-sidebar)" />
                  <path d="M30 18 C24 14, 14 14, 10 18 C14 16, 22 16, 28 20" fill="#F5C542" opacity="0.9" />
                  <path d="M30 22 C26 19, 18 19, 14 22 C18 20, 24 20, 28 24" fill="#D4961C" opacity="0.8" />
                  <path d="M34 18 C40 14, 50 14, 54 18 C50 16, 42 16, 36 20" fill="#F5C542" opacity="0.9" />
                  <path d="M34 22 C38 19, 46 19, 50 22 C46 20, 40 20, 36 24" fill="#D4961C" opacity="0.8" />
                  <path d="M32 48 C22 44, 20 38, 26 34 C20 36, 18 42, 24 46 C18 40, 22 30, 30 28 C24 32, 22 38, 28 42" fill="none" stroke="#F5C542" strokeWidth="2.5" strokeLinecap="round" />
                  <path d="M32 48 C42 44, 44 38, 38 34 C44 36, 46 42, 40 46 C46 40, 42 30, 34 28 C40 32, 42 38, 36 42" fill="none" stroke="#D4961C" strokeWidth="2.5" strokeLinecap="round" />
                  <circle cx="32" cy="10" r="4" fill="#F5C542" />
                  <circle cx="32" cy="10" r="2" fill="#FFF8E1" opacity="0.7" />
                </svg>
              </span>
              <span className="hermes-launch-copy">
                <span className="hermes-launch-title">Hermes WebUI</span>
                <span className="hermes-launch-meta">{t('settings.preferences')}</span>
              </span>
              <span className="hermes-launch-chevron" aria-hidden="true">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="9 18 15 12 9 6" />
                </svg>
              </span>
            </button>
          </div>
        </div>
        
        {/* Resize handle */}
        <div 
          className="resize-handle" 
          onMouseDown={onResizeStart}
          aria-label="Resize sidebar"
          role="separator"
        />
      </aside>
    </>
  );
}
