import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSkillStore } from '@/stores/useSkillStore';
import { useCronStore } from '@/stores/useCronStore';
import { useMemoryStore } from '@/stores/useMemoryStore';
import SkillDetail from '@/components/skills/SkillDetail';
import SkillForm from '@/components/skills/SkillForm';
import TaskDetail from '@/components/tasks/TaskDetail';
import TaskForm from '@/components/tasks/TaskForm';
import MemoryDetail from '@/components/memory/MemoryDetail';
import WorkspaceDetail from '@/components/workspace/WorkspaceDetail';
import WorkspaceForm from '@/components/workspace/WorkspaceForm';
import TodosDrawer from '@/components/layout/TodosDrawer';
import SessionSkillsSelector from '@/components/chat/SessionSkillsSelector';
import SkillsDrawer from '@/components/chat/SkillsDrawer';


interface MainContentProps {
  children: React.ReactNode;
  sidebarWidth?: number;
  rightPanelOpen?: boolean;
}

export default function MainContent({ children }: MainContentProps) {
  const { t } = useTranslation('common');
  const { currentSession } = useSessionStore();
  useWorkspaceStore();
  const [todosPanelOpen, setTodosPanelOpen] = useState(false);
  const [skillsPanelOpen, setSkillsPanelOpen] = useState(false);

  const selectedSkill = useSkillStore((s) => s.selectedSkill);
  const showNewSkillForm = useSkillStore((s) => s.showNewSkillForm);
  const setShowNewSkillForm = useSkillStore((s) => s.setShowNewSkillForm);
  const skillsTabActive = useSkillStore((s) => s.skillsTabActive);

  const tasksTabActive = useCronStore((s) => s.tasksTabActive);
  const selectedJob = useCronStore((s) => s.selectedJob);
  const showNewJobForm = useCronStore((s) => s.showNewJobForm);
  const setShowNewJobForm = useCronStore((s) => s.setShowNewJobForm);
  const showEditJobForm = useCronStore((s) => s.showEditJobForm);
  const setShowEditJobForm = useCronStore((s) => s.setShowEditJobForm);

  const memoryTabActive = useMemoryStore((s) => s.memoryTabActive);

  const workspacesTabActive = useWorkspaceStore((s) => s.workspacesTabActive);
  const showNewWorkspaceForm = useWorkspaceStore((s) => s.showNewWorkspaceForm);
  const setShowNewWorkspaceForm = useWorkspaceStore((s) => s.setShowNewWorkspaceForm);

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

  if (tasksTabActive && showNewJobForm) {
    return (
      <main className="main">
        <TaskForm
          onSuccess={() => setShowNewJobForm(false)}
          onCancel={() => setShowNewJobForm(false)}
        />
      </main>
    );
  }

  if (tasksTabActive && showEditJobForm && selectedJob) {
    return (
      <main className="main">
        <TaskForm
          editJob={selectedJob}
          onSuccess={() => setShowEditJobForm(false)}
          onCancel={() => setShowEditJobForm(false)}
        />
      </main>
    );
  }

  if (tasksTabActive && selectedJob) {
    return (
      <main className="main">
        <TaskDetail />
      </main>
    );
  }

  if (memoryTabActive) {
    return (
      <main className="main">
        <MemoryDetail />
      </main>
    );
  }

  if (workspacesTabActive && showNewWorkspaceForm) {
    return (
      <main className="main">
        <WorkspaceForm
          onSuccess={() => setShowNewWorkspaceForm(false)}
          onCancel={() => setShowNewWorkspaceForm(false)}
        />
      </main>
    );
  }

  if (workspacesTabActive) {
    return (
      <main className="main">
        <WorkspaceDetail />
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
          <SessionSkillsSelector
            onClick={() => setSkillsPanelOpen((v) => !v)}
            isOpen={skillsPanelOpen}
          />
          <button
            className={`chip workspace-toggle-btn ${todosPanelOpen ? 'active' : ''}`}
            onClick={() => setTodosPanelOpen(!todosPanelOpen)}
            title={todosPanelOpen ? t('todos.hide_panel') : t('todos.show_panel')}
            aria-pressed={todosPanelOpen}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="3" y="5" width="6" height="6" rx="1" />
              <path d="m3 17 2 2 4-4" />
              <path d="M13 6h8" />
              <path d="M13 12h8" />
              <path d="M13 18h8" />
            </svg>
            <span className="workspace-toggle-label">{t('todos.title')}</span>
          </button>
        </div>
      </div>
      
      <div className={`main-with-right-panel ${todosPanelOpen || skillsPanelOpen ? 'right-panel-open' : ''}`}>
        <div className="main-chat-area">
          {children}
        </div>
        <SkillsDrawer isOpen={skillsPanelOpen} onClose={() => setSkillsPanelOpen(false)} />
        <TodosDrawer isOpen={todosPanelOpen} onClose={() => setTodosPanelOpen(false)} />
      </div>
    </main>
  );
}
