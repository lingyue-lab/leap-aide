import React, { useCallback, useEffect, useId, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import M3Button from './M3Button';
import { Icon } from './Icons';

/** Input field that the dialog can render between description and actions. */
export type DialogInput =
  | {
      kind: 'number';
      label: string;
      defaultValue: number | string;
      min?: number;
      helperText?: string;
    }
  | {
      kind: 'select';
      label: string;
      defaultValue: string;
      options: Array<{ value: string; label: string }>;
      helperText?: string;
    };

export type DialogSeverity = 'info' | 'warning' | 'danger';

export interface M3DialogProps {
  /** Controlled open state. When false, the dialog renders nothing. */
  open: boolean;
  /** Headline text (M3 headline-small). */
  title: string;
  /** Supporting text under the headline. */
  description?: React.ReactNode;
  /** Severity drives icon container color and confirm button emphasis.
   *  Default 'info' (neutral, primary container icon + primary filled confirm). */
  severity?: DialogSeverity;
  /** 24dp leading icon (Material Symbols ligature string, e.g. "delete"). */
  icon?: string;
  /** Single input field or an array of stacked fields. */
  input?: DialogInput | DialogInput[];
  /** Confirm button label. */
  confirmText: string;
  /** Cancel/dismiss button label. */
  cancelText: string;
  /** Loading state — disables confirm, swaps it to a spinner, blocks Escape. */
  loading?: boolean;
  /** Disables confirm (e.g. Cleanup's "no source selected" gate). */
  confirmDisabled?: boolean;
  /** Slot between description and actions. Cleanup uses it for the
   *  "Deleted N session(s)" success line. */
  footer?: React.ReactNode;
  /** Called when the user confirms. Receives the parsed input value(s)
   *  if `input` was provided, else undefined. */
  onConfirm: (
    value?: number | string | Array<number | string>,
  ) => void | Promise<void>;
  /** Called on cancel (button click, scrim click, or Escape key). */
  onCancel: () => void;
}

interface InputState {
  number: Record<string, number | string>;
  select: Record<string, string>;
}

/** Parse a single input's current string value into the type promised by `onConfirm`. */
function parseInputValue(
  raw: string,
  input: DialogInput,
): number | string {
  if (input.kind === 'number') {
    const parsed = parseInt(raw, 10);
    if (Number.isNaN(parsed)) return 0;
    return Math.max(input.min ?? 0, parsed);
  }
  return raw;
}

/**
 * M3 dialog primitive.
 *
 * Implements the Material Design 3 basic / confirmation dialog specs:
 *   - 28dp corner radius (`--md-shape-xl`)
 *   - surface-container-high background, level-3 elevation shadow
 *   - Optional 24dp leading icon in a colored container
 *   - Min 280dp, max 560dp, 24dp padding
 *   - 32%-opacity scrim (theme-aware via `--scrim`)
 *   - ARIA: role="dialog", aria-modal, aria-labelledby, aria-describedby
 *   - Focus trap (Tab / Shift+Tab cycle between focusable elements)
 *   - Escape closes (unless `loading`); focus is restored to the trigger
 *
 * Renders into document.body via createPortal; returns null when closed.
 */
export default function M3Dialog({
  open,
  title,
  description,
  severity = 'info',
  icon,
  input,
  confirmText,
  cancelText,
  loading = false,
  confirmDisabled = false,
  footer,
  onConfirm,
  onCancel,
}: M3DialogProps) {
  const titleId = useId();
  const descId = useId();

  const inputs: DialogInput[] = useMemo(
    () => (input ? (Array.isArray(input) ? input : [input]) : []),
    [input],
  );

  // Local mirror of each input's current value. Seeded from `input.defaultValue`
  // on mount / when `open` flips true.
  const [inputState, setInputState] = React.useState<InputState>(() => {
    const num: Record<string, number | string> = {};
    const sel: Record<string, string> = {};
    inputs.forEach((inp) => {
      if (inp.kind === 'number') num[String(inp.label)] = inp.defaultValue;
      else sel[String(inp.label)] = inp.defaultValue;
    });
    return { number: num, select: sel };
  });

  // Reset state when reopening so previous values don't linger.
  useEffect(() => {
    if (!open) return;
    const num: Record<string, number | string> = {};
    const sel: Record<string, string> = {};
    inputs.forEach((inp) => {
      if (inp.kind === 'number') num[String(inp.label)] = inp.defaultValue;
      else sel[String(inp.label)] = inp.defaultValue;
    });
    setInputState({ number: num, select: sel });
    // We intentionally only react to `open` — input shape changes are rare
    // and re-seeding on every keystroke would clobber user edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const surfaceRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  // Capture the trigger element on open so we can restore focus on close.
  useEffect(() => {
    if (open) {
      restoreFocusRef.current = (document.activeElement as HTMLElement) ?? null;
    }
  }, [open]);

  // Focus management + Escape handler + body scroll lock.
  useEffect(() => {
    if (!open) return;

    const focusInitial = () => {
      const surface = surfaceRef.current;
      if (!surface) return;
      // First focusable input/select inside, else cancel button.
      const firstField = surface.querySelector<HTMLElement>(
        'input, select, textarea',
      );
      if (firstField) {
        firstField.focus();
      } else {
        cancelRef.current?.focus();
      }
    };
    // Defer one frame so the open animation has a chance to commit.
    const raf = requestAnimationFrame(focusInitial);

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const handleKeyDown = (e: KeyboardEvent) => {
      if (loading) {
        if (e.key === 'Escape') e.preventDefault();
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        onCancel();
        return;
      }
      if (e.key === 'Tab') {
        const surface = surfaceRef.current;
        if (!surface) return;
        const focusables = Array.from(
          surface.querySelectorAll<HTMLElement>(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ),
        ).filter((el) => !el.hasAttribute('data-focus-skip'));
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement as HTMLElement | null;
        if (e.shiftKey && active === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
      // Restore focus to the trigger element.
      const target = restoreFocusRef.current;
      if (target && typeof target.focus === 'function') {
        target.focus();
      }
    };
  }, [open, loading, onCancel]);

  const handleConfirm = useCallback(async () => {
    if (loading || confirmDisabled) return;
    if (inputs.length === 0) {
      await onConfirm(undefined);
      return;
    }
    if (inputs.length === 1) {
      const inp = inputs[0];
      const raw =
        inp.kind === 'number'
          ? String(inputState.number[String(inp.label)] ?? '')
          : inputState.select[String(inp.label)] ?? '';
      await onConfirm(parseInputValue(raw, inp));
      return;
    }
    // Multiple inputs — return an array of parsed values.
    const values: Array<number | string> = inputs.map((inp) => {
      const raw =
        inp.kind === 'number'
          ? String(inputState.number[String(inp.label)] ?? '')
          : inputState.select[String(inp.label)] ?? '';
      return parseInputValue(raw, inp);
    });
    await onConfirm(values);
  }, [inputs, inputState, loading, confirmDisabled, onConfirm]);

  // When the user presses Enter inside an input, treat as confirm.
  const handleInputKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement>,
  ) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      void handleConfirm();
    }
  };

  const handleScrimClick = () => {
    if (loading) return;
    onCancel();
  };

  if (!open) return null;
  if (typeof document === 'undefined') return null;

  const severityClass = `m3-dialog m3-dialog--${severity}`;
  const confirmSeverity = severity === 'danger' ? 'error' : 'primary';

  const dialogNode = (
    <div className="m3-dialog-overlay" onClick={handleScrimClick}>
      <div
        ref={surfaceRef}
        className={severityClass}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description !== undefined ? descId : undefined}
        aria-busy={loading || undefined}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="m3-dialog__header">
          {icon && (
            <span className="m3-dialog__icon" aria-hidden="true">
              <Icon name={icon} size={16} fill={1} />
            </span>
          )}
          <h2 id={titleId} className="m3-dialog__title">
            {title}
          </h2>
        </div>

        {description !== undefined && (
          <div id={descId} className="m3-dialog__body">
            {description}
          </div>
        )}

        {inputs.map((inp, idx) => {
          const key = `${String(inp.label)}-${idx}`;
          if (inp.kind === 'number') {
            return (
              <div className="m3-dialog__field" key={key}>
                <label className="m3-dialog__field-label">{inp.label}</label>
                <input
                  className="m3-dialog__input"
                  type="number"
                  min={inp.min}
                  value={String(inputState.number[String(inp.label)] ?? '')}
                  onChange={(e) => {
                    const labelKey = String(inp.label);
                    setInputState((prev) => ({
                      ...prev,
                      number: { ...prev.number, [labelKey]: e.target.value },
                    }));
                  }}
                  onKeyDown={handleInputKeyDown}
                />
                {inp.helperText && (
                  <span className="m3-dialog__field-help">{inp.helperText}</span>
                )}
              </div>
            );
          }
          return (
            <div className="m3-dialog__field" key={key}>
              <label className="m3-dialog__field-label">{inp.label}</label>
              <select
                className="m3-dialog__select"
                value={inputState.select[String(inp.label)] ?? ''}
                onChange={(e) => {
                  const labelKey = String(inp.label);
                  setInputState((prev) => ({
                    ...prev,
                    select: { ...prev.select, [labelKey]: e.target.value },
                  }));
                }}
              >
                {inp.options.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              {inp.helperText && (
                <span className="m3-dialog__field-help">{inp.helperText}</span>
              )}
            </div>
          );
        })}

        {footer && <div className="m3-dialog__footer">{footer}</div>}

        <div className="m3-dialog__actions">
          <M3Button
            ref={cancelRef}
            variant="text"
            onClick={onCancel}
            disabled={loading}
          >
            {cancelText}
          </M3Button>
          <M3Button
            ref={confirmRef}
            variant="filled"
            severity={confirmSeverity}
            onClick={handleConfirm}
            disabled={loading || confirmDisabled}
            loading={loading}
          >
            {confirmText}
          </M3Button>
        </div>
      </div>
    </div>
  );

  return createPortal(dialogNode, document.body);
}