import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSkillStore } from '@/stores/useSkillStore';
import SkillDetail from '@/components/skills/SkillDetail';
import SkillForm from '@/components/skills/SkillForm';


interface MainContentProps {
  children: React.ReactNode;
  sidebarWidth?: number;
  rightPanelOpen?: boolean;
}

export default function MainContent({ children }: MainContentProps) {
  const { t } = useTranslation('common');
  const { currentSession } = useSessionStore();
  useWorkspaceStore();
  const [workspacePanelOpen, setWorkspacePanelOpen] = useState(false);

  const selectedSkill = useSkillStore((s) => s.selectedSkill);
  const showNewSkillForm = useSkillStore((s) => s.showNewSkillForm);
  const setShowNewSkillForm = useSkillStore((s) => s.setShowNewSkillForm);
  const skillsTabActive = useSkillStore((s) => s.skillsTabActive);

  if (skillsTabActive && showNewSkillForm) {
    return (
      <main className="main">
        <SkillForm
          onSuccess={() => {
            setShowNewSkillForm(false);
            // Auto-select the most recently created skill (last in list after reload)
            // loadSkills is called inside SkillForm before onSuccess, store is already updated
          }}
          onCancel={() => setShowNewSkillForm(false)}
        />
      </main>
    );
  }

  if (skillsTabActive && selectedSkill) {
    return (
      <main className="main">
        <SkillDetail />
      </main>
    );
  }

  const title = currentSession?.title || 'Hermes';
  
  return (
    <main className="main">
      <div className="topbar">
        <button
          className="mobile-hamburger"
          onClick={() => {/* Toggle mobile sidebar */}}
          title="Menu"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="3" y1="6" x2="21" y2="6" />
            <line x1="3" y1="12" x2="21" y2="12" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>
        
        <div className="flex-1 min-w-0 overflow-hidden">
          <div className="topbar-title">{title}</div>
          <div className="topbar-meta">
            {currentSession ? t('chat.active_session') : t('chat.new_conversation')}
          </div>
        </div>
        
        <div className="topbar-chips">
          <button
            className={`chip workspace-toggle-btn ${workspacePanelOpen ? 'active' : ''}`}
            onClick={() => setWorkspacePanelOpen(!workspacePanelOpen)}
            title={workspacePanelOpen ? t('workspace.hide_panel') : t('workspace.show_panel')}
            aria-pressed={workspacePanelOpen}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
            <span className="workspace-toggle-label">{t('workspace.files')}</span>
          </button>
        </div>
      </div>
      
      {children}
    </main>
  );
}
