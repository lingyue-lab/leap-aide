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
    };

let pushRequest: ((req: Request) => void) | null = null;

/**
 * Show a confirmation dialog and resolve to `true` (confirmed) or `false`
 * (cancelled by button, scrim click, or Escape).
 *
 * Drop-in replacement for `window.confirm()` per CLAUDE.md:125.
 */
export function showConfirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    if (!pushRequest) {
      // Host hasn't mounted yet — fail safe by treating as cancel.
      // This shouldn't happen in normal app boot since <DialogHost /> lives
      // at the App root, but guard against misuse during HMR or unit tests.
      // eslint-disable-next-line no-console
      console.warn('showConfirmDialog called before <DialogHost /> mounted');
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
export function showPromptDialog(
  opts: PromptOptions,
): Promise<number | string | null> {
  return new Promise<number | string | null>((resolve) => {
    if (!pushRequest) {
      // eslint-disable-next-line no-console
      console.warn('showPromptDialog called before <DialogHost /> mounted');
      resolve(null);
      return;
    }
    pushRequest({ kind: 'prompt', opts, resolve });
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
    return () => {
      if (pushRequest === push) pushRequest = null;
    };
  }, [push]);

  const handleCancel = useCallback(() => {
    if (!request) return;
    if (request.kind === 'confirm') request.resolve(false);
    else request.resolve(null);
    setRequest(null);
  }, [request]);

  const handleConfirm = useCallback(
    (value?: number | string | Array<number | string>) => {
      if (!request) return;
      if (request.kind === 'confirm') {
        request.resolve(true);
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