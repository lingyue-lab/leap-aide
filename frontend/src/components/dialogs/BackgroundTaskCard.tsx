import { useEffect, useState, useRef } from 'react';
import { useSessionStore } from '@/stores/useSessionStore';

export interface BackgroundTaskInfo {
  session_id: string;
  title: string;
  created_at: number;
  workspace?: string;
  stream_id: string;
  prompt: string;
}

const AUTO_DISMISS_MS = 10000;

// Module-level setter — set by the component on mount, cleared on unmount.
let _setTask: ((task: BackgroundTaskInfo) => void) | null = null;

/** Show a background-task-started card.  Safe to call before the component mounts. */
export function showBackgroundTaskCard(task: BackgroundTaskInfo) {
  if (_setTask) {
    _setTask(task);
  }
}

export default function BackgroundTaskCard() {
  const [visible, setVisible] = useState(false);
  const [task, setTask] = useState<BackgroundTaskInfo | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    _setTask = (t: BackgroundTaskInfo) => {
      setTask(t);
      setVisible(true);
      // Auto-dismiss
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        setVisible(false);
        setTask(null);
      }, AUTO_DISMISS_MS);
    };
    return () => {
      _setTask = null;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleView = () => {
    if (!task) return;
    const { selectSession } = useSessionStore.getState();
    selectSession(task.session_id);
    window.history.pushState(null, '', `?session=${encodeURIComponent(task.session_id)}`);
    setVisible(false);
    setTask(null);
    if (timerRef.current) clearTimeout(timerRef.current);
  };

  const handleDismiss = () => {
    setVisible(false);
    setTask(null);
    if (timerRef.current) clearTimeout(timerRef.current);
  };

  if (!visible || !task) return null;

  const promptText = task.title.startsWith('bg: ') ? task.title.slice(4) : task.title;

  return (
    <div className={`bg-task-card ${visible ? 'visible' : ''}`}>
      <div className="bg-task-inner">
        <div className="bg-task-header">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
          <span>Background Task</span>
          <button className="bg-task-close" onClick={handleDismiss} aria-label="Dismiss" title="Dismiss">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div className="bg-task-desc">{promptText}</div>
        <div className="bg-task-btns">
          <button className="bg-task-btn view" onClick={handleView}>
            View
          </button>
        </div>
      </div>
    </div>
  );
}
