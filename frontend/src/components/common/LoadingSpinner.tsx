import { useTranslation } from 'react-i18next';

interface LoadingSpinnerProps {
  /** Display mode:
   *  - inline    : fits in flow next to siblings (button label, list row)
   *  - overlay   : covers the nearest positioned parent; pointer-events: none
   *  - fullscreen: fixed-position M3 scrim with a centered card; blocks
   *                all other interaction. Used for operations whose
   *                server-side latency is large (e.g. /api/session/pin
   *                synchronously boots SessionDisplay on the server,
   *                taking 10-15s on cold start) so the user has an
   *                unambiguous "your click registered" signal. */
  mode?: 'inline' | 'overlay' | 'fullscreen';
  /** Spinner diameter in pixels. M3 spec defines three sizes for the
   *  circular progress indicator:
   *    - 24dp compact  → inline (button labels, list rows, dense UI)
   *    - 40dp standard → overlay (loading scrim over content)
   *    - 48dp hero     → fullscreen (blocking operations)
   *  Defaults below pick the matching M3 size per mode. Callers that
   *  need a smaller variant in very dense contexts (e.g. file tree
   *  labels) can still pass a custom size. */
  size?: number;
  /** Optional loading text. When provided, shown in the layout;
   *  when undefined, no text element is rendered. Pass the empty
   *  string explicitly if you want a default i18n fallback suppressed
   *  but the slot kept. */
  text?: string;
  /** Hide text and show only spinner */
  minimal?: boolean;
}

/**
 * Unified loading spinner component.
 *
 * Implements the M3 Circular Progress Indicator (indeterminate variant)
 * per https://m3.material.io/components/progress-indicators/specs.
 *
 * Two SVG elements form the indicator:
 *   - .m3-circular-progress__track — a full circle at 24% opacity that
 *     stays visible behind the active indicator (M3 requires the
 *     track to remain present in indeterminate mode).
 *   - .m3-circular-progress__arc   — a stroked circle that the
 *     `m3-cp-sweep` keyframe pulses (visible length 1 → 100 → 1) while
 *     `m3-cp-rotate` spins it 360deg around the SVG center. The arc
 *     has stroke-linecap="round" (M3 default) and shares the track's
 *     r=18 / stroke-width=4, so its cap geometry matches the track
 *     stroke exactly — no drift off the rail.
 *   - color: --md-sys-color-primary.
 *
 * The same SVG is reused for all three modes; the wrapper handles
 * placement (inline / overlay / fullscreen).
 */
export default function LoadingSpinner({
  mode = 'inline',
  size,
  text,
  minimal = false,
}: LoadingSpinnerProps) {
  const { t } = useTranslation('common');
  const defaultSize = mode === 'fullscreen' ? 48 : mode === 'overlay' ? 40 : 24;
  const spinnerSize = size ?? defaultSize;
  // text is truly optional: undefined → render no text element at all.
  // Callers that want a default string supply one explicitly.
  const showText = text !== undefined;
  const displayText = text ?? '';
  // Accessible label falls back to the shared i18n `loading` string
  // when no explicit text is given, so screen readers still hear
  // something meaningful even though no text is visually shown.
  const a11yLabel = text ?? (t('loading') || 'Loading...');

  const indicator = (
    <M3CircularProgress
      size={spinnerSize}
      label={a11yLabel}
      minimal={minimal}
    />
  );

  if (mode === 'fullscreen') {
    // M3 progress indicator overlay: a scrim with the spinner
    // centered. No card wrapper — matches the inline/overlay
    // modes (both spinner-only) so the three loading UIs read as
    // the same component, just at different scopes. Border, padding,
    // border-radius, and elevation all dropped — they belonged to
    // the old dialog-card pattern, not M3's progress overlay spec.
    return (
      <div
        className="loading-spinner-fullscreen"
        role="status"
        aria-live="polite"
        aria-busy="true"
        data-testid="loading-spinner-fullscreen"
      >
        {indicator}
      </div>
    );
  }

  if (mode === 'overlay') {
    return (
      <div className="loading-spinner-overlay" role="progressbar" aria-label={a11yLabel}>
        <div className="loading-spinner-overlay-content">{indicator}</div>
      </div>
    );
  }

  // Inline mode
  return (
    <span className="loading-spinner-inline" role="progressbar" aria-label={a11yLabel}>
      {indicator}
      {!minimal && showText && (
        <span className="loading-spinner-text">{displayText}</span>
      )}
    </span>
  );
}

interface M3CircularProgressProps {
  size: number;
  label: string;
  minimal?: boolean;
}

/**
 * M3 indeterminate circular progress indicator.
 *
 * SVG approach (no JS animation library required):
 *   - viewBox 0 0 48 48 with cx=24, cy=24, r=18 — leaves 6 units of
 *     margin so the 4-unit stroke never clips the viewBox edge.
 *   - one stroked circle for the arc; the `m3-cp-sweep` keyframe
 *     pulses stroke-dasharray so the visible arc length grows and
 *     shrinks (1 → 100 → 1 units of the ~113-unit circumference),
 *     while `m3-cp-rotate` simultaneously spins the whole arc 360deg
 *     over 2s. The two animations have mismatched periods (1.4s vs
 *     2s) — that's what gives M3's characteristic "uneven orbital"
 *     feel; a single period would just look like a smooth sweep.
 *   - the arc and track share cx, cy, r, and stroke-width, so their
 *     centerline is identical (r=18) and both render r=16 to r=20.
 *     Round linecap (M3 default) extends each arc end tangentially
 *     by 2 units but the radial extent matches the track, so the
 *     arc visually rides the track — never falls off.
 */
function M3CircularProgress({ size, label }: M3CircularProgressProps) {
  return (
    <svg
      className="m3-circular-progress"
      width={size}
      height={size}
      viewBox="0 0 48 48"
      role="img"
      aria-label={label}
    >
      {/* M3 track — a full circle at 24% opacity. Provides the
          "circular track" backdrop that M3 spec requires for the
          indeterminate variant: the full circumference stays faintly
          visible behind the active indicator. */}
      <circle
        className="m3-circular-progress__track"
        cx="24"
        cy="24"
        r="18"
        fill="none"
        strokeWidth="4"
      />
      {/* M3 active arc — stroked circle with stroke-dasharray
          animated by `m3-cp-sweep` and rotation by `m3-cp-rotate`.
          The centerline is r=18 and stroke-width is 4, identical to
          the track, so the cap endpoints align with the track's
          inner/outer radius by construction. transform-origin is
          24px 24px in viewBox coordinates (no `transform-box:
          fill-box`!), so the rotation pivot is the SVG geometric
          center — see CSS comment for why this matters. */}
      <circle
        className="m3-circular-progress__arc"
        cx="24"
        cy="24"
        r="18"
        fill="none"
        strokeWidth="4"
        strokeDasharray="1 200"
      />
    </svg>
  );
}