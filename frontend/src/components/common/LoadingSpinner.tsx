import { useTranslation } from 'react-i18next';

interface LoadingSpinnerProps {
  /** Display mode: overlay covers parent, inline fits in flow */
  mode?: 'overlay' | 'inline';
  /** Spinner size in pixels (default 20 for inline, 27 for overlay) */
  size?: number;
  /** Optional loading text shown below spinner */
  text?: string;
  /** Hide text and show only spinner */
  minimal?: boolean;
}

/**
 * Unified loading spinner component (M3 Circular Progress Indicator).
 * Uses Material Symbols `progress_activity` icon with spin animation.
 */
export default function LoadingSpinner({
  mode = 'inline',
  size,
  text,
  minimal = false,
}: LoadingSpinnerProps) {
  const { t } = useTranslation('common');
  const spinnerSize = size ?? (mode === 'overlay' ? 27 : 20);
  const displayText = text ?? (t('common.loading') || 'Loading...');

  if (mode === 'overlay') {
    return (
      <div className="loading-spinner-overlay" role="progressbar" aria-label={displayText}>
        <div className="loading-spinner-overlay-content">
          <span
            className="material-symbols-rounded loading-spinner-icon"
            style={{
              fontSize: spinnerSize,
              fontVariationSettings: "'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24",
            }}
          >
            progress_activity
          </span>
          {!minimal && (
            <span className="loading-spinner-text">{displayText}</span>
          )}
        </div>
      </div>
    );
  }

  // Inline mode
  return (
    <div className="loading-spinner-inline" role="progressbar" aria-label={displayText}>
      <span
        className="material-symbols-rounded loading-spinner-icon"
        style={{
          fontSize: spinnerSize,
          fontVariationSettings: "'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24",
        }}
      >
        progress_activity
      </span>
      {!minimal && (
        <span className="loading-spinner-text">{displayText}</span>
      )}
    </div>
  );
}
