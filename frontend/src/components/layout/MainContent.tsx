import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react';
import { flushSync } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSkillStore } from '@/stores/useSkillStore';
import { useCronStore } from '@/stores/useCronStore';
import { useMemoryStore } from '@/stores/useMemoryStore';
import { useTerminalStore } from '@/stores/useTerminalStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { useModelsStore } from '@/stores/useModelsStore';
import SkillDetail from '@/components/skills/SkillDetail';
import SkillForm from '@/components/skills/SkillForm';
import TaskDetail from '@/components/tasks/TaskDetail';
import TaskForm from '@/components/tasks/TaskForm';
import MemoryDetail from '@/components/memory/MemoryDetail';
import WorkspaceDetail from '@/components/workspace/WorkspaceDetail';
import WorkspaceForm from '@/components/workspace/WorkspaceForm';
import TodosDrawer from '@/components/layout/TodosDrawer';
// Lazy-load TerminalView so xterm (~150-200KB + 30KB CSS) is only fetched
// when the user actually opens a terminal tab.
const TerminalView = lazy(() => import('@/components/terminal/TerminalView'));

import SettingsDetail from '@/components/settings/SettingsDetail';
import { EmptyState, TASKS_ICON, SKILLS_ICON, MEMORY_ICON, WORKSPACE_ICON } from '@/components/common/Placeholder';
import { observerService } from '@/services/api';
import Tooltip from '@/components/common/Tooltip';
import { Icon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ChatComposer from '@/components/chat/ChatComposer';
import { useChatStream } from '@/hooks/useChatStream';
import { gateProviders } from '@/utils/providersGate';


interface MainContentProps {
  children: React.ReactNode;
  sidebarWidth?: number;
  rightPanelOpen?: boolean;
}

/**
 * Welcome state — placeholder Session created client-side by
 * `enterPendingNewSession`. The composer is centered above a greeting;
 * no topbar, no chips, no TodosDrawer. First send flows through
 * `startStream` which triggers backend auto-create (api/routes.py:
 * _handle_chat_start) and then `promotePendingToReal` swaps the
 * placeholder for the real session.
 *
 * Lives in its own component so `useChatStream` is mounted only while
 * welcome state is active — keeping the unconditional hooks of
 * NonTerminalContent stable for all other views (chat, cron jobs,
 * skills, memory, workspaces, settings).
 */
function WelcomeState() {
  const { t } = useTranslation(['common', 'chat']);
  const { currentSession, pendingNewSession, pendingNewSessionPromoting, enterPendingNewSession } = useSessionStore();
  const { startStream } = useChatStream();

  // Ensure a placeholder Session exists while welcome state is rendered.
  // NonTerminalContent routes here when EITHER `pendingNewSession` is set OR
  // `currentSessionId` is null (no session at all). The latter covers:
  //   • first load with zero sessions in db
  //   • user deleted the last session
  //   • a background session-loading path left currentSessionId=null
  // Without this guard, `pendingNewSession` stays null, handleWelcomeSend
  // reads `undefined` for `placeholderSid`, returns early, and the send
  // button is silently dead — clicking it fires no POST /api/chat/start.
  useEffect(() => {
    if (pendingNewSession) return;
    // Ensure the workspace list is loaded first so the placeholder resolves
    // the built-in Default workspace for the composer footer chip.  App boot
    // also loads it; this guard covers the first-load race where the
    // placeholder would otherwise get no workspace at all.
    const wsState = useWorkspaceStore.getState();
    if (wsState.workspacesLoaded) {
      enterPendingNewSession();
    } else {
      wsState.loadWorkspaces().then(() => enterPendingNewSession());
    }
  }, [pendingNewSession, enterPendingNewSession]);

  // Eagerly fetch the model catalog so the picker shows a default selection
  // on first render.  Without this, settings.model can point at an
  // unconfigured provider (e.g. user only has DeepSeek configured but
  // settings.default_model is anthropic/...) and the menu opens with
  // nothing preselected.  Goes through the shared store (issue #16) so a
  // welcome-page mount joins the composer's in-flight request instead of
  // stacking a second one on a cold backend.
  const firstAvailableModelId = useModelsStore((s) => s.data?.groups?.[0]?.models?.[0]?.id);
  const loadModels = useModelsStore((s) => s.load);
  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  // Track the user's explicit model pick from the picker. The composer
  // can't POST /api/session/update for the placeholder session (it would
  // 404 — there's no backend row yet), so we capture the pick here and
  // forward it to startStream. After the first send, the real session is
  // created with this model and we sync it into the sessionStore so
  // subsequent renders reflect the choice.
  const [welcomeModelId, setWelcomeModelId] = useState<string | undefined>(undefined);
  const handleWelcomeModelChange = useCallback((modelId: string) => {
    setWelcomeModelId(modelId);
  }, []);

  const handleWelcomeSend = useCallback(async (text: string, attachments?: string[], mode?: string) => {
    // Re-read the store — the mount-time useEffect above may have just
    // populated pendingNewSession, so a stale closure from a render where
    // pendingNewSession was null would otherwise bail here.
    const placeholderSid = useSessionStore.getState().pendingNewSession?.session_id;
    if (!placeholderSid || !text.trim()) return;

    // Gate: refuse to send the first welcome message until at least one
    // model provider is configured. Without this the user sees a 401
    // mid-stream and has no clear path to fix it.
    let outcome: 'passed' | 'navigated' = 'passed';
    try {
      outcome = await gateProviders();
    } catch {
      outcome = 'passed';
    }
    if (outcome !== 'passed') return;
    // Resolve the model to use for this turn.  Order:
    //   1. welcomeModelId — user's explicit pick in the picker (highest
    //      priority; honors what they actually clicked)
    //   2. firstAvailableModelId — the eager-load fallback that is
    //      guaranteed to be in the provider catalog (the user has keys
    //      for it).  settings.model is unreliable when settings.default_model
    //      points at an unconfigured provider — e.g. user only has
    //      DeepSeek but default_model is anthropic/claude-opus-4.6 — and
    //      the backend would 401 mid-stream.
    //   3. hard-coded fallback
    const model = welcomeModelId ?? firstAvailableModelId ?? 'anthropic/claude-sonnet-4-5';
    await startStream(placeholderSid, text, model, currentSession?.workspace, attachments, mode);
  }, [pendingNewSession, currentSession, startStream, firstAvailableModelId, welcomeModelId]);

  const handleWelcomeCancel = useCallback(() => {
    // Welcome is a single-way door (per design decision): Escape/cancel is no-op.
  }, []);

  return (
    <div className="main-chat-area welcome-state">
      <div className="welcome-stack">
        <div className="welcome-header">
          <Icon name="support_agent" size={64} />
          <h1 className="welcome-greeting">{t('welcome.greeting', { ns: 'chat' })}</h1>
        </div>
        {/* B.6 — show a spinner while the placeholder is being promoted to a
            real session.  Window is POST /api/chat/start + StreamManager
            reconnect (with B.1/B.2 it's now ~50-100ms LAN), but a spinner
            gives instant feedback so the user doesn't think their click
            was lost.  Sits above the composer per M3 guidance so the
            greeting above stays visible and reassuring. */}
        {pendingNewSessionPromoting && (
          <div className="welcome-state__promoting" aria-live="polite">
            <LoadingSpinner
              mode="inline"
              size={36}
              text={t('welcome.creating', { ns: 'chat' }) || t('loading')}
            />
          </div>
        )}
        <ChatComposer
          onSend={handleWelcomeSend}
          onCancel={handleWelcomeCancel}
          cardVisible={false}
          className="composer-wrap--centered"
          placeholderText={t('composer.placeholder', { ns: 'chat' })}
          defaultModelId={firstAvailableModelId}
          onModelChange={handleWelcomeModelChange}
        />
      </div>
    </div>
  );
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
  const { t } = useTranslation(['common', 'chat']);
  const { currentSession, currentSessionId, pendingNewSession } = useSessionStore();

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

  // Gate: trigger the providers precondition check on the initial mount
  // when the user lands directly on a gated view (chat or cron jobs).
  // NavTabs.switchPanel handles tab-click navigation, so this effect only
  // runs once per "entry" — when the route / session view first renders
  // and the user lands on chat via `/` or `/session/:id`. We use a ref
  // to ensure we don't fire on every tab flag change (only on entry).
  //
  // Note: this component is only mounted when terminalTabActive is false
  // (the parent switches between two parallel <main> elements), so we
  // don't need to check the terminal flag here.
  const gateFiredRef = useRef(false);
  useEffect(() => {
    const isChatView =
      !settingsTabActive &&
      !skillsTabActive &&
      !memoryTabActive &&
      !workspacesTabActive;
    const isTasksView = tasksTabActive;
    const isGatedView = isChatView || isTasksView;

    if (!isGatedView) {
      // Reset so re-entering a gated view (e.g. switching back to chat)
      // will fire the gate again.
      gateFiredRef.current = false;
      return;
    }

    if (gateFiredRef.current) return;
    gateFiredRef.current = true;

    gateProviders().catch(() => {
      gateFiredRef.current = false;
    });
  }, [
    settingsTabActive,
    skillsTabActive,
    memoryTabActive,
    workspacesTabActive,
    tasksTabActive,
  ]);

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

  // ── Welcome state: pendingNewSession is a local-only placeholder; no topbar,
  // no chips, no TodosDrawer. The composer is centered and a greeting is shown
  // above it. First send goes through startStream which triggers the backend
  // auto-create (api/routes.py:_handle_chat_start), then promotePendingToReal
  // swaps the placeholder for the real session and re-renders the chat view.
  // The actual rendering lives in <WelcomeState/> so useChatStream is mounted
  // only while welcome state is active.
  //
  // Also covers the no-session-at-all case (currentSessionId === null on first
  // load / after a delete).  Previously ChatInterface rendered its own
  // EmptyState icon for this; now WelcomeState owns the entire "no active
  // session" presentation, so the chat panel never has a separate empty view.
  if (pendingNewSession || !currentSessionId) {
    return <WelcomeState />;
  }

  // ── Default: chat view ──────────────────────────────────────────────────────
  const title = currentSession?.is_subagent_session
    ? t('sessions.subagent_title', { time: new Date((currentSession.created_at || 0) * 1000).toLocaleString() })
    : currentSession?.title || 'LeapAide';

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
              <Suspense fallback={<LoadingSpinner mode="inline" text="Loading terminal..." />}>
                <TerminalView terminalId={t.id} />
              </Suspense>
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
