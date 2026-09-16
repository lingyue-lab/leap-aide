import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

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
 * Uses lucide-react `Loader2` (rotating circular arrow) with spin animation.
 */
export default function LoadingSpinner({
  mode = 'inline',
  size,
  text,
  minimal = false,
}: LoadingSpinnerProps) {
  const { t } = useTranslation('common');
  const spinnerSize = size ?? (mode === 'overlay' ? 27 : 20);
  const displayText = text ?? (t('loading') || 'Loading...');

  if (mode === 'overlay') {
    return (
      <div className="loading-spinner-overlay" role="progressbar" aria-label={displayText}>
        <div className="loading-spinner-overlay-content">
          <Loader2
            className="loading-spinner-icon"
            size={spinnerSize}
            aria-hidden="true"
          />
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
      <Loader2
        className="loading-spinner-icon"
        size={spinnerSize}
        aria-hidden="true"
      />
      {!minimal && (
        <span className="loading-spinner-text">{displayText}</span>
      )}
    </div>
  );
}