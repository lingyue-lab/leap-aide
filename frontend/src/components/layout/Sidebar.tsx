import { useState } from 'react';
import NavTabs from '@/components/sidebar/NavTabs';
import SessionList from '@/components/sidebar/SessionList';
import TasksPanel from '@/components/sidebar/TasksPanel';
import SkillsPanel from '@/components/sidebar/SkillsPanel';
import MemoryPanel from '@/components/sidebar/MemoryPanel';
import WorkspacesPanel from '@/components/sidebar/WorkspacesPanel';
import TerminalPanel from '@/components/sidebar/TerminalPanel';
import VncPanel from '@/components/vnc/VncPanel';
import SettingsPanel from '@/components/sidebar/SettingsPanel';
import type { NavPanel } from '@/types';

interface SidebarProps {
  width?: number;
  onResizeStart?: (e: React.MouseEvent) => void;
}

export default function Sidebar({ width = 300, onResizeStart }: SidebarProps) {
  const [activePanel, setActivePanel] = useState<NavPanel>('chat');
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const closeSidebar = () => setSidebarOpen(false);

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

          <div className={`panel-view ${activePanel === 'settings' ? 'active' : ''}`} id="panelSettings">
            <SettingsPanel />
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
