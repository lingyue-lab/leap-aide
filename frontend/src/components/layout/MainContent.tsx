import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSkillStore } from '@/stores/useSkillStore';
import { useCronStore } from '@/stores/useCronStore';
import { useMemoryStore } from '@/stores/useMemoryStore';
import { useTerminalStore } from '@/stores/useTerminalStore';
import { useVncStore } from '@/stores/useVncStore';
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
import TerminalView from '@/components/terminal/TerminalView';
import VncView from '@/components/vnc/VncView';


interface MainContentProps {
  children: React.ReactNode;
  sidebarWidth?: number;
  rightPanelOpen?: boolean;
}

/**
 * Determine which non-terminal main content to render.
 * Returns the content node or null (for chat view which uses `children`).
 */
function NonTerminalContent({
  children,
  todosPanelOpen,
  skillsPanelOpen,
  setTodosPanelOpen,
  setSkillsPanelOpen,
}: {
  children: React.ReactNode;
  todosPanelOpen: boolean;
  skillsPanelOpen: boolean;
  setTodosPanelOpen: (v: boolean) => void;
  setSkillsPanelOpen: (v: boolean) => void;
}) {
  const { t } = useTranslation('common');
  const { currentSession } = useSessionStore();

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

  // ── Non-chat content views ──────────────────────────────────────────────────

  if (skillsTabActive && showNewSkillForm) {
    return (
      <SkillForm
        onSuccess={() => setShowNewSkillForm(false)}
        onCancel={() => setShowNewSkillForm(false)}
      />
    );
  }
  if (skillsTabActive && selectedSkill) {
    return <SkillDetail />;
  }
  if (tasksTabActive && showNewJobForm) {
    return (
      <TaskForm
        onSuccess={() => setShowNewJobForm(false)}
        onCancel={() => setShowNewJobForm(false)}
      />
    );
  }
  if (tasksTabActive && showEditJobForm && selectedJob) {
    return (
      <TaskForm
        editJob={selectedJob}
        onSuccess={() => setShowEditJobForm(false)}
        onCancel={() => setShowEditJobForm(false)}
      />
    );
  }
  if (tasksTabActive && selectedJob) {
    return <TaskDetail />;
  }
  if (memoryTabActive) {
    return <MemoryDetail />;
  }
  if (workspacesTabActive && showNewWorkspaceForm) {
    return (
      <WorkspaceForm
        onSuccess={() => setShowNewWorkspaceForm(false)}
        onCancel={() => setShowNewWorkspaceForm(false)}
      />
    );
  }
  if (workspacesTabActive) {
    return <WorkspaceDetail />;
  }

  // ── Default: chat view ──────────────────────────────────────────────────────
  const title = currentSession?.title || 'Hermes';

  return (
    <>
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
            onClick={() => setSkillsPanelOpen(!skillsPanelOpen)}
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
    </>
  );
}

export default function MainContent({ children }: MainContentProps) {
  const [todosPanelOpen, setTodosPanelOpen] = useState(false);
  const [skillsPanelOpen, setSkillsPanelOpen] = useState(false);
  useWorkspaceStore(); // subscribe for side effects

  const terminalTabActive = useTerminalStore((s) => s.terminalTabActive);
  const activeTerminalId = useTerminalStore((s) => s.activeTerminalId);
  const terminals = useTerminalStore((s) => s.terminals);
  const selectTerminal = useTerminalStore((s) => s.selectTerminal);
  const addTerminal = useTerminalStore((s) => s.addTerminal);

  const vncTabActive = useVncStore((s) => s.vncTabActive);

  return (
    <>
      {/* ─────────────────────────────────────────────────────────────────────
          Terminal area: ALWAYS mounted so bash processes survive tab switches.
          Visibility is controlled purely with CSS display.
      ──────────────────────────────────────────────────────────────────────── */}
      <main
        className="main terminal-main"
        style={{ display: terminalTabActive ? 'flex' : 'none' }}
        aria-hidden={!terminalTabActive}
      >
        {/* Tab bar */}
        <div className="terminal-tab-bar">
          {terminals.map((t) => (
            <button
              key={t.id}
              className={`terminal-tab ${activeTerminalId === t.id ? 'active' : ''}`}
              onClick={() => selectTerminal(t.id)}
              title={t.name}
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="4 17 10 11 4 5" />
                <line x1="12" y1="19" x2="20" y2="19" />
              </svg>
              <span>{t.name}</span>
            </button>
          ))}
          <button
            className="terminal-tab terminal-tab-add"
            onClick={addTerminal}
            title="New Terminal"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        </div>

        {/* All terminal instances kept mounted; CSS toggles visibility */}
        <div className="terminal-views-container">
          {terminals.map((t) => (
            <div
              key={t.id}
              className={`terminal-instance ${activeTerminalId === t.id ? 'active' : ''}`}
            >
              <TerminalView terminalId={t.id} />
            </div>
          ))}
        </div>
      </main>

      {/* ─────────────────────────────────────────────────────────────────────
          Non-terminal area: hidden while terminal tab is active.
      ──────────────────────────────────────────────────────────────────────── */}
      <main
        className="main"
        style={{ display: terminalTabActive || vncTabActive ? 'none' : undefined }}
      >
        <NonTerminalContent
          todosPanelOpen={todosPanelOpen}
          skillsPanelOpen={skillsPanelOpen}
          setTodosPanelOpen={setTodosPanelOpen}
          setSkillsPanelOpen={setSkillsPanelOpen}
        >
          {children}
        </NonTerminalContent>
      </main>

      {/* ─────────────────────────────────────────────────────────────────────
          VNC area: ALWAYS mounted so the WebSocket stays alive on tab switch.
          Visibility is controlled purely with CSS display.
      ──────────────────────────────────────────────────────────────────────── */}
      <main
        className="main vnc-main"
        style={{ display: vncTabActive ? 'flex' : 'none' }}
        aria-hidden={!vncTabActive}
      >
        <VncView />
      </main>
    </>
  );
}
