/**
 * notify.ts — Shared notification utilities (sound + desktop notification)
 *
 * Used by both session streaming completion and cron job completion.
 */

import { useSettingsStore } from '@/stores/useSettingsStore';

// ── Notification sound (Web Audio API — no external file needed) ──────────────
let _audioCtx: AudioContext | null = null;

export function playNotificationBeep() {
  try {
    if (!_audioCtx) {
      _audioCtx = new AudioContext();
    }
    const ctx = _audioCtx;
    const now = ctx.currentTime;

    // Two-tone beep: pleasant "ding-ding" notification sound
    const osc1 = ctx.createOscillator();
    const osc2 = ctx.createOscillator();
    const gain = ctx.createGain();

    osc1.type = 'sine';
    osc1.frequency.setValueAtTime(880, now); // A5 note
    osc2.type = 'sine';
    osc2.frequency.setValueAtTime(1100, now + 0.15); // higher second tone

    gain.gain.setValueAtTime(0.3, now);
    gain.gain.setValueAtTime(0.3, now + 0.15);
    gain.gain.exponentialRampToValueAtTime(0.01, now + 0.35);

    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(ctx.destination);

    osc1.start(now);
    osc1.stop(now + 0.15);
    osc2.start(now + 0.15);
    osc2.stop(now + 0.35);
  } catch (e) {
    // Web Audio not available or context blocked — silently ignore
  }
}

// ── Desktop notification ──────────────────────────────────────────────────────

export interface NotifyOptions {
  title: string;
  body: string;
  tag?: string;
}

/**
 * Send a desktop (OS-level) notification if the user has enabled notifications
 * and granted browser permission.
 */
export function sendDesktopNotification(opts: NotifyOptions) {
  const settings = useSettingsStore.getState().settings;
  if (!settings?.notifications_enabled) return;

  if (typeof Notification === 'undefined') return;
  if (Notification.permission !== 'granted') return;

  new Notification(opts.title, {
    body: opts.body,
    tag: opts.tag,
  });
}

/**
 * Convenience: play sound + send desktop notification for a completion event.
 * Respects user settings (sound_enabled, notifications_enabled).
 */
export function notifyCompletion(opts: NotifyOptions) {
  const settings = useSettingsStore.getState().settings;

  if (settings?.sound_enabled !== false) {
    playNotificationBeep();
  }

  sendDesktopNotification(opts);
}