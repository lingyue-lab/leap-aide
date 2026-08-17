import { useEffect, useState } from 'react';
import { useSessionStore } from '@/stores/useSessionStore';

export interface BackgroundTaskInfo {
  session_id: string;
  title: string;
  created_at: number;
  workspace?: string;
  stream_id: string;
  prompt: string;
}

// Per-session Map for card state storage
const _cards = new Map<string, BackgroundTaskInfo>();

// Current session ID
let _currentSessionId: string | null = null;

// Visible-change callback
let _onVisibleChange: ((visible: boolean) => void) | null = null;

// Internal setters
let _renderTask: ((task: BackgroundTaskInfo | null) => void) | null = null;
let _renderVisible: ((v: boolean) => void) | null = null;

/** Show a background-task-started card for a specific session.
 *  If sessionId ≠ current session, stores but does not display. */
export function showBackgroundTaskCard(sessionId: string, task: BackgroundTaskInfo) {
  _cards.set(sessionId, task);

  if (sessionId === _currentSessionId) {
    _renderTask?.(task);
    _renderVisible?.(true);
    _onVisibleChange?.(true);
  }
}

/** Dismiss the background task card for a specific session. */
export function dismissBackgroundTaskCard(sessionId: string) {
  _cards.delete(sessionId);
  if (sessionId === _currentSessionId) {
    _renderTask?.(null);
    _renderVisible?.(false);
    _onVisibleChange?.(false);
  }
}

/** Register a callback that fires when card visibility changes. */
export function onBackgroundTaskCardVisibleChange(fn: (visible: boolean) => void): () => void {
  _onVisibleChange = fn;
  return () => { _onVisibleChange = null; };
}

/** Notify BackgroundTaskCard of a session change. */
export function setBgTaskCardCurrentSessionId(sessionId: string | null) {
  _currentSessionId = sessionId;
  if (sessionId) {
    const pending = _cards.get(sessionId);
    if (pending) {
      _renderTask?.(pending);
      _renderVisible?.(true);
      _onVisibleChange?.(true);
    } else {
      _renderVisible?.(false);
      _onVisibleChange?.(false);
    }
  } else {
    _renderVisible?.(false);
    _onVisibleChange?.(false);
  }
}

export default function BackgroundTaskCard() {
  const [visible, setVisible] = useState(false);
  const [task, setTask] = useState<BackgroundTaskInfo | null>(null);

  useEffect(() => {
    _renderTask = (t: BackgroundTaskInfo | null) => {
      setTask(t);
    };
    _renderVisible = (v: boolean) => {
      setVisible(v);
    };
    return () => {
      _renderTask = null;
      _renderVisible = null;
    };
  }, []);

  const handleView = () => {
    if (!task) return;
    const { selectSession } = useSessionStore.getState();
    selectSession(task.session_id);
    window.history.pushState(null, '', `?session=${encodeURIComponent(task.session_id)}`);
    handleDismiss();
  };

  const handleDismiss = () => {
    if (!_currentSessionId) return;
    _cards.delete(_currentSessionId);
    setVisible(false);
    setTask(null);
    _onVisibleChange?.(false);
  };

  if (!visible || !task) return null;

  const promptText = task.title.startsWith('bg: ') ? task.title.slice(4) : task.title;

  return (
    <div className={`bg-task-card ${visible ? 'visible' : ''}`}>
      <div className="bg-task-inner">
        <div className="bg-task-content">
          <div className="bg-task-desc">
            <span>Background Task</span>
            {promptText && <div style={{ marginTop: 4, opacity: 0.7 }}>{promptText}</div>}
          </div>
          <div className="bg-task-sider">
            <div className="bg-task-btns">
              <button className="bg-task-btn bg-task-btn--primary" onClick={handleView}>View</button>
              <button className="bg-task-close" onClick={handleDismiss} aria-label="Dismiss">Close</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
