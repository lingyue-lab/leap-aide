import { useEffect, useState, useCallback } from 'react';
import { chatService } from '@/services/api';

let _refresh: (() => void) | null = null;
export function refreshGoalStatus() { _refresh?.(); }

interface GoalState {
  goal: string;
  status: string;
  turns_used: number;
  max_turns: number;
  subgoals: number;
  subgoal_texts?: string[];
  paused_reason?: string;
}

interface Props {
  sessionId: string | null;
}

export default function GoalStatusBar({ sessionId }: Props) {
  const [goal, setGoal] = useState<GoalState | null>(null);

  const fetchGoal = useCallback(async () => {
    if (!sessionId) { setGoal(null); return; }
    try {
      const data = await chatService.getGoalStatus(sessionId);
      if (data.goal) setGoal(data as GoalState);
      else setGoal(null);
    } catch { setGoal(null); }
  }, [sessionId]);

  // Initial fetch + poll every 10s
  useEffect(() => {
    _refresh = fetchGoal;
    fetchGoal();
    const iv = setInterval(fetchGoal, 10000);
    return () => { _refresh = null; clearInterval(iv); };
  }, [fetchGoal]);

  // Refresh after stream completes (listen for bg-stream-done)
  useEffect(() => {
    const handler = () => fetchGoal();
    window.addEventListener('bg-stream-done', handler);
    return () => window.removeEventListener('bg-stream-done', handler);
  }, [fetchGoal]);

  if (!goal) return null;

  const statusIcon = goal.status === 'active' ? '⊙' : goal.status === 'paused' ? '⏸' : '✓';
  const statusLabel = goal.status === 'active' ? 'Active' : goal.status === 'paused' ? 'Paused' : goal.status;
  const extra = goal.paused_reason ? ` — ${goal.paused_reason}` : '';

  const subTexts = goal.subgoal_texts || [];

  return (
    <div className="queue-status-bar" style={{ borderColor: 'rgba(100, 200, 150, 0.3)', flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}>
      <div className="queue-status-left">
        <span className="queue-status-icon">{statusIcon}</span>
        <span className="queue-status-text">
          Goal: {goal.goal.length > 60 ? goal.goal.slice(0, 60) + '…' : goal.goal}
        </span>
        <span className="queue-status-divider">·</span>
        <span className="queue-status-hint">
          {statusLabel} · {goal.turns_used}/{goal.max_turns} turns{goal.subgoals > 0 ? ` · ${goal.subgoals} subgoal(s)` : ''}{extra}
        </span>
      </div>
      {subTexts.length > 0 && (
        <div style={{ paddingLeft: 22, fontSize: 11, color: 'var(--muted)', lineHeight: 1.6 }}>
          {subTexts.map((t, i) => (
            <div key={i}>subgoal-{i + 1}: {t}</div>
          ))}
        </div>
      )}
    </div>
  );
}
