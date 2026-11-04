/**
 * showToast — request a brief M3 snackbar message.
 *
 * Reuses the `show-toast` CustomEvent already dispatched by
 * stores/streamManager.ts, so stream/agent notices and UI confirmations
 * share one presentation layer (components/common/Snackbar.tsx).
 */
export interface ToastOptions {
  /** Visible time in ms. M3 snackbars should last 4–10s. Default 4000. */
  duration?: number;
}

export function showToast(message: string, opts: ToastOptions = {}): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(
    new CustomEvent('show-toast', {
      detail: { message, duration: opts.duration ?? 4000 },
    })
  );
}
