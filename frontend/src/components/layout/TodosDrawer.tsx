import { useTranslation } from 'react-i18next';
import { Icon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useTodos } from '@/hooks/useTodos';
import type { TodoItem } from '@/hooks/useTodos';

interface TodosDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

// ── Status icons ─────────────────────────────────────────────────────────────

function IconPending() {
  return <Icon name="check_box_outline_blank" size={18} />;
}

function IconInProgress() {
  return <LoadingSpinner mode="inline" minimal size={18} />;
}

function IconCompleted() {
  return <Icon name="check" size={18} />;
}

function IconCancelled() {
  return <Icon name="close" size={18} />;
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
      className="todo-row"
      style={{ background: 'transparent' }}
    >
      <span className="todo-status-icon" style={{ color }}>
        {STATUS_ICON[todo.status] ?? <IconPending />}
      </span>
      <div className="flex-1 min-w-0">
        <div
          className="todo-content"
          style={{
            color: isCompleted || isCancelled ? 'var(--muted)' : 'var(--text)',
            textDecoration: isCompleted || isCancelled ? 'line-through' : 'none',
            opacity: isCompleted || isCancelled ? 0.6 : 1,
          }}
        >
          {todo.content}
        </div>
        <div
          className="todo-meta"
        >
          {todo.id} · {todo.status}
        </div>
      </div>
    </div>
  );
}

export default function TodosDrawer({ isOpen, onClose }: TodosDrawerProps) {
  const { t } = useTranslation('common');
  // useTodos() resolves from messages (post-stream) OR toolCalls.args (during streaming)
  const todos = useTodos();
  const completedCount = todos.filter((x) => x.status === 'completed').length;

  return (
    <aside className={`right-panel ${isOpen ? 'open' : ''}`}>
      <div className="resize-handle" />

      {/* Header */}
      <div className="panel-header">
        <span className="font-semibold">{t('todos.title')}</span>
        {todos.length > 0 && (
          <span
            className="text-xs tabular-nums ml-2"
            style={{ color: 'var(--muted)', opacity: 0.8 }}
          >
            {completedCount}/{todos.length}
          </span>
        )}
        <div className="panel-actions">
          <button
            className="panel-icon-btn"
            onClick={onClose}
            title={t('todos.hide_panel')}
          >
            <Icon name="close" size={18} />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="overflow-y-auto flex-1">
        {todos.length === 0 ? (
          <p
            className="italic text-center py-8"
            style={{ color: 'var(--muted)', fontSize: '16px', fontWeight: 400, letterSpacing: '0.25px' }}
          >
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
    </aside>
  );
}
