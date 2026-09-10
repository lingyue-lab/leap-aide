import { useTranslation } from 'react-i18next';
import type { ReactNode } from 'react';
import CustomScrollbar from './CustomScrollbar';

interface FormActionsProps {
  onSubmit: () => void;
  onCancel: () => void;
  /** When true, submit shows busyLabel and both buttons are disabled. */
  busy?: boolean;
  /** Extra condition to disable the submit button (e.g. empty required field). */
  submitDisabled?: boolean;
  submitLabel?: ReactNode;
  busyLabel?: ReactNode;
}

/** Standard topbar action pair: primary (submit) + Cancel. */
export function FormActions({
  onSubmit,
  onCancel,
  busy,
  submitDisabled,
  submitLabel,
  busyLabel,
}: FormActionsProps) {
  const { t } = useTranslation('common');
  const addLabel = submitLabel ?? t('add');
  const addingLabel = busyLabel ?? t('adding');
  return (
    <>
      <button
        className="cron-btn run text-sm"
        onClick={onSubmit}
        disabled={busy || submitDisabled}
      >
        {busy ? addingLabel : addLabel}
      </button>
      <button className="cron-btn text-sm" onClick={onCancel} disabled={busy}>
        {t('cancel')}
      </button>
    </>
  );
}

interface FormPageProps {
  title: string;
  /** Error message shown as an accent chip in the topbar (left of actions). */
  error?: string;
  /** Header action buttons rendered in the topbar chips (primary + cancel). */
  actions?: ReactNode;
  children: ReactNode;
}

/** Shared create/edit page shell: topbar (title + header actions) + scrollable body. */
export default function FormPage({ title, error, actions, children }: FormPageProps) {
  return (
    <div className="flex flex-col overflow-hidden" style={{ flex: 1 }}>
      <div className="topbar">
        <div className="topbar-title">{title}</div>
        <div className="topbar-chips">
          {error && (
            <span className="text-sm text-[var(--accent)] self-center max-w-[280px] truncate">{error}</span>
          )}
          {actions}
        </div>
      </div>
      <CustomScrollbar className="flex-1">
        <div className="p-6 flex flex-col gap-4 max-w-2xl mx-auto w-full">
          {children}
        </div>
      </CustomScrollbar>
    </div>
  );
}
