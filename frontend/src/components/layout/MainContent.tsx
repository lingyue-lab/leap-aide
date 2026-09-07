import { useState, useEffect } from 'react';
import { flushSync } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSkillStore } from '@/stores/useSkillStore';
import { useCronStore } from '@/stores/useCronStore';
import { useMemoryStore } from '@/stores/useMemoryStore';
import { useTerminalStore } from '@/stores/useTerminalStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import SkillDetail from '@/components/skills/SkillDetail';
import SkillForm from '@/components/skills/SkillForm';
import TaskDetail from '@/components/tasks/TaskDetail';
import TaskForm from '@/components/tasks/TaskForm';
import MemoryDetail from '@/components/memory/MemoryDetail';
import WorkspaceDetail from '@/components/workspace/WorkspaceDetail';
import WorkspaceForm from '@/components/workspace/WorkspaceForm';
import TodosDrawer from '@/components/layout/TodosDrawer';
import TerminalView from '@/components/terminal/TerminalView';

import SettingsDetail from '@/components/settings/SettingsDetail';
import { EmptyState, TASKS_ICON, SKILLS_ICON, MEMORY_ICON, WORKSPACE_ICON } from '@/components/common/Placeholder';
import { observerService } from '@/services/api';
import Tooltip from '@/components/common/Tooltip';
import { Icon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';


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
  sessionLoading,
  toggleTodos,
  closeAllPanels,
}: {
  children: React.ReactNode;
  todosPanelOpen: boolean;
  sessionLoading: boolean;
  toggleTodos: () => void;
  closeAllPanels: () => void;
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
  const selectedMemoryFile = useMemoryStore((s) => s.selectedFile);

  const workspacesTabActive = useWorkspaceStore((s) => s.workspacesTabActive);
  const currentWorkspace = useWorkspaceStore((s) => s.currentWorkspace);
  const showNewWorkspaceForm = useWorkspaceStore((s) => s.showNewWorkspaceForm);
  const setShowNewWorkspaceForm = useWorkspaceStore((s) => s.setShowNewWorkspaceForm);

  const settingsTabActive = useSettingsTabStore((s) => s.settingsTabActive);

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
  if (memoryTabActive && selectedMemoryFile) {
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
  if (workspacesTabActive && currentWorkspace) {
    return <WorkspaceDetail />;
  }
  if (settingsTabActive) {
    return <SettingsDetail />;
  }

  // ── Panel placeholders (tab active but no item selected) ────────────────────
  if (tasksTabActive) {
    return <EmptyState hintsKey="empty.tasks_hints">{TASKS_ICON}</EmptyState>;
  }
  if (skillsTabActive) {
    return <EmptyState hintsKey="empty.skills_hints">{SKILLS_ICON}</EmptyState>;
  }
  if (memoryTabActive) {
    return <EmptyState hintsKey="empty.memory_hints">{MEMORY_ICON}</EmptyState>;
  }
  if (workspacesTabActive) {
    return <EmptyState hintsKey="empty.workspaces_hints">{WORKSPACE_ICON}</EmptyState>;
  }

  // ── Default: chat view ──────────────────────────────────────────────────────
  const title = currentSession?.is_subagent_session
    ? t('sessions.subagent_title', { time: new Date((currentSession.created_at || 0) * 1000).toLocaleString() })
    : currentSession?.title || 'Leap Aide';

  return (
    <>
      <div className="main-with-right-panel">
        <div className="main-chat-area">
          {!sessionLoading && (
            <div className="topbar">
            <button
              className="mobile-hamburger"
              onClick={() => {/* Toggle mobile sidebar */}}
              title="Menu"
            >
              <Icon name="menu" size={24} />
            </button>

            <div className="flex-1 min-w-0 overflow-hidden">
              <div className="topbar-title">{title}</div>
              <div className="topbar-meta">
                {currentSession ? currentSession.session_id : 'Beyond assistance.'}
              </div>
            </div>

            <div className="topbar-chips">
              {currentSession && (() => {
                const isReadonly = !!currentSession._readonly;
                const isPinned = !!currentSession.pinned;
                const canObserve = !isReadonly && isPinned;
                const observeTooltip = isReadonly
                  ? t('vnc.readonly_no_observe', 'Read-only session — observation not available')
                  : !isPinned
                    ? t('vnc.pin_to_observe', 'Pin session to observe')
                    : t('vnc.observe', 'Observe');
                const todosTooltip = isReadonly
                  ? t('todos.readonly_no_todos', 'Read-only session — todos not available')
                  : todosPanelOpen
                    ? t('todos.hide_panel')
                    : t('todos.show_panel');

                return (
                  <>
                    <Tooltip text={todosTooltip}>
                      <button
                        className={`icon-btn${todosPanelOpen ? ' active' : ''}`}
                        onClick={() => { if (!isReadonly) toggleTodos(); }}
                        disabled={isReadonly}
                        aria-pressed={todosPanelOpen}
                      >
                        <Icon name="checklist" size={18} />
                      </button>
                    </Tooltip>
                    <Tooltip text={observeTooltip}>
                      <button
                        className="icon-btn"
                        onClick={canObserve ? async () => {
                          const sid = currentSession.session_id;
                          if (!sid) return;
                          try { await observerService.getInfo(sid); } catch { /* ignore */ }
                          window.open('/observations/' + encodeURIComponent(sid), '_blank');
                        } : undefined}
                        disabled={!canObserve || isReadonly}
                      >
                        <Icon name="visibility" size={18} />
                      </button>
                    </Tooltip>
                  </>
                );
              })()}
            </div>
          </div>
          )}
          {children}
          {sessionLoading && <LoadingSpinner mode="overlay" />}
        </div>
        <TodosDrawer isOpen={todosPanelOpen} onClose={() => closeAllPanels()} />
      </div>
    </>
  );
}

export default function MainContent({ children }: MainContentProps) {
  const [todosPanelOpen, setTodosPanelOpen] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(false);

  const toggleTodos = () => { setTodosPanelOpen((v) => !v); };
  const closeAllPanels = () => { setTodosPanelOpen(false); };
  useWorkspaceStore(); // subscribe for side effects

  // Listen for session loading state from ChatInterface.
  // Use flushSync to ensure immediate visual feedback during session switch.
  useEffect(() => {
    const handler = (e: Event) => {
      const loading = (e as CustomEvent).detail as boolean;
      // flushSync ensures synchronous React state update for immediate overlay
      flushSync(() => {
        setSessionLoading(loading);
      });
    };
    window.addEventListener('hermes:session-loading', handler);
    return () => window.removeEventListener('hermes:session-loading', handler);
  }, []);

  const terminalTabActive = useTerminalStore((s) => s.terminalTabActive);
  // Close panels when switching to terminal
  useEffect(() => { if (terminalTabActive) closeAllPanels(); }, [terminalTabActive]);
  const activeTerminalId = useTerminalStore((s) => s.activeTerminalId);
  const terminals = useTerminalStore((s) => s.terminals);

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
        style={{ display: terminalTabActive ? 'none' : undefined }}
      >
        <NonTerminalContent
          todosPanelOpen={todosPanelOpen}
          sessionLoading={sessionLoading}
          toggleTodos={toggleTodos}
          closeAllPanels={closeAllPanels}
        >
          {children}
        </NonTerminalContent>
      </main>

    </>
  );
}
