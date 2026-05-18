import { useTranslation } from 'react-i18next';
import { useTodos } from '@/hooks/useTodos';
import type { TodoItem } from '@/hooks/useTodos';

// ── Status icons ──────────────────────────────────────────────────────────────

function IconPending() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="2" />
    </svg>
  );
}

function IconInProgress() {
  return (
    <svg
      width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      aria-hidden="true"
      style={{ animation: 'spin 1s linear infinite' }}
    >
      <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
      <path d="M12 2a10 10 0 0 1 10 10" />
    </svg>
  );
}

function IconCompleted() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function IconCancelled() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <IconPending />,
  in_progress: <IconInProgress />,
  completed: <IconCompleted />,
  cancelled: <IconCancelled />,
};

const STATUS_COLOR: Record<string, string> = {
  pending: 'var(--muted)',
  in_progress: 'var(--blue, #3b82f6)',
  completed: 'rgba(100,200,100,.85)',
  cancelled: 'rgba(200,100,100,.5)',
};

function TodoRow({ todo }: { todo: TodoItem }) {
  const color = STATUS_COLOR[todo.status] ?? 'var(--muted)';
  const isCompleted = todo.status === 'completed';
  const isCancelled = todo.status === 'cancelled';

  return (
    <div
      className="flex items-start gap-2 py-1.5 px-2 rounded-lg"
      style={{ background: 'var(--input-bg)' }}
    >
      <span className="flex-shrink-0 mt-0.5 flex items-center" style={{ color }}>
        {STATUS_ICON[todo.status] ?? <IconPending />}
      </span>
      <div className="flex-1 min-w-0">
        <div
          className="text-sm leading-snug break-words"
          style={{
            color: isCompleted || isCancelled ? 'var(--muted)' : 'var(--text)',
            textDecoration: isCompleted || isCancelled ? 'line-through' : 'none',
            opacity: isCompleted || isCancelled ? 0.6 : 1,
          }}
        >
          {todo.content}
        </div>
        <div
          className="text-[10px] mt-0.5 truncate"
          style={{ color: 'var(--muted)', opacity: 0.5 }}
        >
          {todo.id} · {todo.status}
        </div>
      </div>
    </div>
  );
}

export default function TodosPanel() {
  const { t } = useTranslation('common');
  // useTodos() resolves from messages (post-stream) OR toolCalls.args (during streaming)
  const todos = useTodos();
  const completedCount = todos.filter((x) => x.status === 'completed').length;

  return (
    <div className="p-3">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
          {t('todos.title')}
        </div>
        {todos.length > 0 && (
          <span className="text-xs text-[var(--muted)] tabular-nums">
            {completedCount}/{todos.length}
          </span>
        )}
      </div>

      {todos.length === 0 ? (
        <p className="text-[var(--muted)] text-sm italic text-center py-4">
          {t('todos.empty')}
        </p>
      ) : (
        <div className="space-y-1">
          {todos.map((todo) => (
            <TodoRow key={todo.id} todo={todo} />
          ))}
        </div>
      )}
    </div>
  );
}
