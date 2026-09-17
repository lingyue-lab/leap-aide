import React, { forwardRef } from 'react';
import LoadingSpinner from './LoadingSpinner';

export type M3ButtonVariant = 'filled' | 'text';
export type M3ButtonSeverity = 'primary' | 'error';
export type M3ButtonSize = 'md' | 'sm';

export interface M3ButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  /** M3 button variant. Default 'text' (M3 dialog action default). */
  variant?: M3ButtonVariant;
  /** Color severity. Default 'primary'. Use 'error' for destructive actions. */
  severity?: M3ButtonSeverity;
  /** Button height. 'md' = 40dp M3 default; 'sm' = 32dp for dense rows. */
  size?: M3ButtonSize;
  /** Leading icon (any React node, usually <Icon name="…" />). */
  startIcon?: React.ReactNode;
  /**
   * When true, swaps the leading icon for an inline LoadingSpinner and
   * disables the button to prevent double submission. The button keeps
   * its width so surrounding layout doesn't jump.
   */
  loading?: boolean;
  children: React.ReactNode;
}

/**
 * M3 button primitive.
 *
 * Implements Material Design 3 button specs (m3.material.io/components/buttons):
 *   - 40dp height for size='md'; 32dp for size='sm'.
 *   - State layers via the `::after` pseudo-element (hover 8%, focus 12%,
 *     pressed 12% of onSurface), matching the existing `.btn-filled` style
 *     that seeded this component.
 *   - 'text' is the M3 default for dialog actions; 'filled' is used for the
 *     primary action of a flow.
 *   - 'severity=error' swaps the fill/text color to the error palette
 *     (used by destructive confirms such as Delete Session).
 *
 * The two variants this component exposes cover every dialog action in the
 * sidebar today. `outlined`, `elevated`, and `tonal` are intentionally
 * omitted — they will be added when a real consumer needs them.
 */
const M3Button = forwardRef<HTMLButtonElement, M3ButtonProps>(function M3Button(
  {
    variant = 'text',
    severity = 'primary',
    size = 'md',
    startIcon,
    loading = false,
    disabled,
    className,
    type = 'button',
    children,
    onClick,
    onKeyDown,
    ...rest
  },
  ref,
) {
  const classes = [
    'm3-btn',
    `m3-btn--${variant}`,
    `m3-btn--${size}`,
    severity === 'error' ? 'm3-btn--severity-error' : 'm3-btn--severity-primary',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  const isDisabled = disabled || loading;

  // Enter on a loading button is a no-op (prevents re-submission), but we
  // still let the browser receive the keydown so it doesn't fall through
  // to a parent handler unexpectedly.
  const handleKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (loading && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
    }
    onKeyDown?.(e);
  };

  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    if (loading) {
      e.preventDefault();
      return;
    }
    onClick?.(e);
  };

  return (
    <button
      ref={ref}
      type={type}
      className={classes}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      {...rest}
    >
      {loading ? (
        <LoadingSpinner mode="inline" minimal size={size === 'sm' ? 14 : 16} />
      ) : (
        startIcon
      )}
      <span>{children}</span>
    </button>
  );
});

export default M3Button;