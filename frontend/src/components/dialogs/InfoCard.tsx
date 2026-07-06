import { useEffect, useState, useRef } from 'react';

const AUTO_DISMISS_MS = 15000;

interface InfoCardOptions {
  sticky?: boolean;
  loading?: boolean;
  onDismiss?: () => void;
}

let _show: ((content: string, options?: InfoCardOptions) => void) | null = null;
let _dismiss: (() => void) | null = null;

/** Show an informational card. Safe to call before the component mounts.
 *  Options:
 *    sticky  — keep visible until user dismisses (no auto-dismiss)
 *    loading — show a spinner instead of content
 *    onDismiss — called when user clicks close */
export function showInfoCard(content: string, options?: InfoCardOptions) {
  if (_show) _show(content, options);
}

/** Dismiss the currently visible info card. Safe to call before mount. */
export function dismissInfoCard() {
  if (_dismiss) _dismiss();
}

/** Renders a markdown-ish content string as simple HTML-safe text with basic formatting. */
function renderContent(text: string): string {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^### (.*$)/gm, '<h3>$1</h3>')
    .replace(/^## (.*$)/gm, '<h2>$1</h2>')
    .replace(/^# (.*$)/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>')
    .replace(/\n/g, '<br/>')
    .replace(/(https?:\/\/[^\s<>]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
}

export default function InfoCard() {
  const [visible, setVisible] = useState(false);
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onDismissRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    _show = (text: string, options?: InfoCardOptions) => {
      setContent(text);
      setLoading(options?.loading ?? false);
      setVisible(true);
      onDismissRef.current = options?.onDismiss ?? null;
      if (timerRef.current) clearTimeout(timerRef.current);
      if (!options?.sticky && !options?.loading) {
        timerRef.current = setTimeout(() => {
          setVisible(false);
          setContent('');
          setLoading(false);
        }, AUTO_DISMISS_MS);
      }
    };
    _dismiss = () => {
      setVisible(false);
      setContent('');
      setLoading(false);
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    return () => {
      _show = null;
      _dismiss = null;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const handleDismiss = () => {
    setVisible(false);
    setContent('');
    setLoading(false);
    if (timerRef.current) clearTimeout(timerRef.current);
    if (onDismissRef.current) {
      onDismissRef.current();
      onDismissRef.current = null;
    }
  };

  if (!visible || !content) return null;

  return (
    <div className={`bg-task-card ${visible ? 'visible' : ''}`}>
      <div className="bg-task-inner">
        <div className="bg-task-header" style={{ color: 'var(--muted)' }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
          <span>Info</span>
          <button className="bg-task-close" onClick={handleDismiss} aria-label="Dismiss" title="Dismiss">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        {loading ? (
          <div className="bg-task-desc" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}>
              <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
              <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
            </svg>
            <span>{content}</span>
          </div>
        ) : (
          <div
            className="bg-task-desc"
            dangerouslySetInnerHTML={{ __html: renderContent(content) }}
          />
        )}
      </div>
    </div>
  );
}
