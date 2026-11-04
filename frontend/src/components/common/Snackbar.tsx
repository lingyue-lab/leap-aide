import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

/**
 * M3 snackbar: brief, non-modal feedback about a completed action
 * (e.g. "Saved"), anchored bottom-center, auto-dismissing.
 *
 * Spec notes (https://m3.material.io/components/snackbar):
 * - Inverse-surface fill with inverse-on-surface text.
 * - 4dp (shape-xs) corners, level-3 elevation.
 * - Shown for 4–10s, one at a time; a new message replaces the current one.
 * - Non-modal and non-interactive here: no action button is needed for a
 *   simple confirmation, so it must not steal pointer events.
 */
interface ToastDetail {
  message?: string;
  duration?: number;
}

export default function Snackbar() {
  const [message, setMessage] = useState('');
  const [visible, setVisible] = useState(false);
  const hideTimer = useRef<number | null>(null);

  useEffect(() => {
    const onToast = (event: Event) => {
      const detail = (event as CustomEvent<ToastDetail>).detail || {};
      if (!detail.message) return;
      setMessage(detail.message);
      setVisible(true);
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
      hideTimer.current = window.setTimeout(
        () => setVisible(false),
        detail.duration ?? 4000
      );
    };

    window.addEventListener('show-toast', onToast);
    return () => {
      window.removeEventListener('show-toast', onToast);
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    };
  }, []);

  if (typeof document === 'undefined' || !message) return null;

  return createPortal(
    <div
      className={`m3-snackbar${visible ? ' m3-snackbar--visible' : ''}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      {message}
    </div>,
    document.body
  );
}
