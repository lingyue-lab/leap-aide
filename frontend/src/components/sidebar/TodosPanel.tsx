import { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface TodoItem {
  id: string;
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
}

export default function TodosPanel() {
  const { t } = useTranslation('common');
  const [todos, setTodos] = useState<TodoItem[]>([]);

  const addTodo = () => {
    const newTodo: TodoItem = {
      id: Date.now().toString(),
      content: '',
      status: 'pending',
    };
    setTodos([...todos, newTodo]);
  };

  const updateTodo = (id: string, updates: Partial<TodoItem>) => {
    setTodos(todos.map((t) => (t.id === id ? { ...t, ...updates } : t)));
  };

  const deleteTodo = (id: string) => {
    setTodos(todos.filter((t) => t.id !== id));
  };

  return (
    <div className="p-3">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">{t('todos.title')}</div>
        <button className="icon-btn" onClick={addTodo} title={t('todos.new')}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>
      </div>
      <div className="space-y-2">
        {todos.map((todo) => (
          <div key={todo.id} className="flex items-center gap-2 p-2 rounded-lg bg-[var(--input-bg)]">
            <input
              type="checkbox"
              checked={todo.status === 'completed'}
              onChange={() => updateTodo(todo.id, { 
                status: todo.status === 'completed' ? 'pending' : 'completed' 
              })}
            />
            <input
              type="text"
              value={todo.content}
              onChange={(e) => updateTodo(todo.id, { content: e.target.value })}
              placeholder={t('todos.placeholder')}
              className="flex-1 bg-transparent border-none outline-none text-sm"
            />
            <button className="icon-btn" onClick={() => deleteTodo(todo.id)}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        ))}
        {todos.length === 0 && (
          <p className="text-[var(--muted)] text-sm italic text-center py-4">{t('todos.empty')}</p>
        )}
      </div>
    </div>
  );
}
