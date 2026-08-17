import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from './Icons';

interface EmptyStateProps {
  children: React.ReactNode;
  hintsKey?: string;
  translationNs?: string;
}

/**
 * Generic empty-state component used by all panels (Chat, Tasks, Skills,
 * Memory, Workspaces) when no item is selected.
 *
 * Renders an icon (passed as children) above a randomly chosen hint.
 * The hint re-randomises when `hintsKey` changes (panel switch).
 */
export function EmptyState({
  children,
  hintsKey = 'empty.hints',
  translationNs = 'common',
}: EmptyStateProps) {
  const { t } = useTranslation(translationNs);
  const hint = useMemo(() => {
    try {
      const pool = t(hintsKey, { returnObjects: true }) as unknown as string[];
      if (Array.isArray(pool) && pool.length > 0) {
        return pool[Math.floor(Math.random() * pool.length)];
      }
    } catch { /* fall through */ }
    return '';
  }, [hintsKey, t]);

  return (
    <div className="empty-state">
      <div className="empty-logo">{children}</div>
      {hint && <p className="empty-hint">{hint}</p>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  M3-compliant empty-state icons (Material Symbols, 64×64)          */
/* ------------------------------------------------------------------ */

/** Chat bubble — used by the Chat panel empty state. */
export const CHAT_ICON = <Icon name="support_agent" size={64} />;

/** Calendar / scheduled-job — used by the Tasks (Cron Jobs) panel empty state. */
export const TASKS_ICON = <Icon name="calendar_month" size={64} />;

/** Layers / stack — used by the Skills panel empty state. */
export const SKILLS_ICON = <Icon name="layers" size={64} />;

/** Brain — used by the Memory panel empty state. */
export const MEMORY_ICON = <Icon name="psychology" size={64} />;

/** Folder — used by the Workspaces panel empty state. */
export const WORKSPACE_ICON = <Icon name="folder_open" size={64} />;
