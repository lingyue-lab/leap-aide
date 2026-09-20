import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import M3Dialog, {
  type DialogInput,
  type DialogSeverity,
} from './M3Dialog';

/** Options for showConfirmDialog(). All fields except title are optional. */
export interface ConfirmOptions {
  title: string;
  description?: React.ReactNode;
  severity?: DialogSeverity;
  icon?: string;
  /** Defaults to "Confirm" via i18n; pass an explicit string to override. */
  confirmText?: string;
  /** Defaults to "Cancel" via i18n; pass an explicit string to override. */
  cancelText?: string;
}

/** Options for showAlertDialog() — M3 Basic Dialog (single primary action,
 *  no Cancel button, no Escape / scrim dismissal). Use when the user has
 *  no safe way to cancel (e.g. the providers gate where chat / cron is
 *  unreachable without configuring a key). All fields except title are
 *  optional. */
export interface AlertOptions {
  title: string;
  description?: React.ReactNode;
  severity?: DialogSeverity;
  icon?: string;
  /** Defaults to "OK" via i18n; pass an explicit string to override. */
  confirmText?: string;
}

/** Options for showPromptDialog(). `input` is required. */
export interface PromptOptions {
  title: string;
  description?: React.ReactNode;
  severity?: DialogSeverity;
  icon?: string;
  input: DialogInput;
  confirmText?: string;
  cancelText?: string;
}

/** Internal request shape that the host renders. */
type Request =
  | { kind: 'confirm'; opts: ConfirmOptions; resolve: (v: boolean) => void }
  | {
      kind: 'prompt';
      opts: PromptOptions;
      resolve: (v: number | string | null) => void;
    }
  | { kind: 'alert'; opts: AlertOptions; resolve: () => void };

let pushRequest: ((req: Request) => void) | null = null;

// DialogHost mount registration. React flushes useEffects in deepest-first
// order, so a gate fired from a deeply-nested component (e.g. the
// NonTerminalContent useEffect inside MainContent inside Layout) can run
// BEFORE this module's host component mounts and installs `pushRequest`.
// Without a way to wait, the gate's dialog would silently resolve and the
// user would be navigated straight to settings instead of seeing the
// confirmation dialog.
//
// `whenDialogHostReady()` resolves when the host is mounted; show helpers
// `await` it before pushing, so the dialog appears as expected regardless
// of mount order.
let readyResolve: () => void = () => {};
let readyPromise: Promise<void> = new Promise<void>((r) => {
  readyResolve = r;
});

export function whenDialogHostReady(): Promise<void> {
  return pushRequest ? Promise.resolve() : readyPromise;
}

/**
 * Show a confirmation dialog and resolve to `true` (confirmed) or `false`
 * (cancelled by button, scrim click, or Escape).
 *
 * Drop-in replacement for `window.confirm()` per CLAUDE.md:125.
 */
export async function showConfirmDialog(opts: ConfirmOptions): Promise<boolean> {
  await whenDialogHostReady();
  return new Promise<boolean>((resolve) => {
    if (!pushRequest) {
      // Host unmounted between the ready-promise resolving and now — rare
      // (e.g. HMR mid-dialog); treat as cancel.
      // eslint-disable-next-line no-console
      console.warn('showConfirmDialog called after <DialogHost /> unmounted');
      resolve(false);
      return;
    }
    pushRequest({ kind: 'confirm', opts, resolve });
  });
}

/**
 * Show a prompt dialog with a single number or select field. Resolves to the
 * parsed value on confirm, `null` on cancel / scrim / Escape.
 */
export async function showPromptDialog(
  opts: PromptOptions,
): Promise<number | string | null> {
  await whenDialogHostReady();
  return new Promise<number | string | null>((resolve) => {
    if (!pushRequest) {
      // eslint-disable-next-line no-console
      console.warn('showPromptDialog called after <DialogHost /> unmounted');
      resolve(null);
      return;
    }
    pushRequest({ kind: 'prompt', opts, resolve });
  });
}

/**
 * Show a Basic Dialog (M3): single primary action, no Cancel button,
 * Escape and scrim-click cannot dismiss. Resolves when the user clicks
 * the primary button (or the dialog is unmounted externally).
 *
 * Use this for hard gates where the user has no way to safely cancel —
 * e.g. the providers gate forces chat / cron to be unreachable without
 * a configured key, so a Cancel option would just leave them stuck.
 */
export async function showAlertDialog(opts: AlertOptions): Promise<void> {
  await whenDialogHostReady();
  return new Promise<void>((resolve) => {
    if (!pushRequest) {
      // eslint-disable-next-line no-console
      console.warn('showAlertDialog called after <DialogHost /> unmounted');
      resolve();
      return;
    }
    pushRequest({ kind: 'alert', opts, resolve });
  });
}

/**
 * Singleton host that lives at the App root. The module-level `pushRequest`
 * setter installed by `push()` lets `showConfirmDialog()` / `showPromptDialog()`
 * queue requests from anywhere without prop drilling or React context.
 *
 * Only one dialog is shown at a time; new requests while one is open queue
 * after the current one resolves (no nested rendering, no scroll-jacking).
 */
export default function DialogHost() {
  const { t } = useTranslation('common');
  const [request, setRequest] = useState<Request | null>(null);

  const push = useCallback((req: Request) => {
    setRequest(req);
  }, []);

  useEffect(() => {
    pushRequest = push;
    // Wake any show*Dialog callers that were waiting on host registration.
    // We do this synchronously inside this effect so the promise resolves
    // before any subsequent show*Dialog call returns.
    readyResolve();
    return () => {
      if (pushRequest === push) {
        pushRequest = null;
        // Reset the ready promise so re-mounts (HMR) get a fresh await.
        readyPromise = new Promise<void>((r) => {
          readyResolve = r;
        });
      }
    };
  }, [push]);

  const handleCancel = useCallback(() => {
    if (!request) return;
    if (request.kind === 'confirm') request.resolve(false);
    else if (request.kind === 'prompt') request.resolve(null);
    // 'alert' kind has no cancel — ignore stray calls.
    setRequest(null);
  }, [request]);

  const handleConfirm = useCallback(
    (value?: number | string | Array<number | string>) => {
      if (!request) return;
      if (request.kind === 'confirm') {
        request.resolve(true);
      } else if (request.kind === 'alert') {
        request.resolve();
      } else {
        // Prompt helper exposes a single input — first value wins if the
        // caller somehow passes an array.
        if (Array.isArray(value)) {
          request.resolve(value[0] ?? null);
        } else {
          request.resolve((value as number | string | undefined) ?? null);
        }
      }
      setRequest(null);
    },
    [request],
  );

  if (!request) return null;

  if (request.kind === 'confirm') {
    return (
      <M3Dialog
        open
        severity={request.opts.severity ?? 'info'}
        icon={request.opts.icon}
        title={request.opts.title}
        description={request.opts.description}
        confirmText={request.opts.confirmText ?? t('confirm')}
        cancelText={request.opts.cancelText ?? t('cancel')}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />
    );
  }

  if (request.kind === 'alert') {
    return (
      <M3Dialog
        open
        severity={request.opts.severity ?? 'info'}
        icon={request.opts.icon}
        title={request.opts.title}
        description={request.opts.description}
        confirmText={request.opts.confirmText ?? t('ok')}
        cancelText="" // unused — required by M3Dialog prop type
        hideCancel
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />
    );
  }

  return (
    <M3Dialog
      open
      severity={request.opts.severity ?? 'info'}
      icon={request.opts.icon}
      title={request.opts.title}
      description={request.opts.description}
      input={request.opts.input}
      confirmText={request.opts.confirmText ?? t('confirm')}
      cancelText={request.opts.cancelText ?? t('cancel')}
      onConfirm={handleConfirm}
      onCancel={handleCancel}
    />
  );
}