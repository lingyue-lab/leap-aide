import { useEffect, useState, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { clarifyService } from '@/services/api';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { showInfoCard } from '@/components/dialogs/InfoCard';
import i18n from '@/i18n/config';
import type { ClarifyRequest } from '@/types';

// Per-session Map for clarify state storage.
// Includes receivedAt so the auto-dismiss timeout is based on when the
// clarification was RECEIVED, not when the card became visible.
interface StoredClarify {
  detail: ClarifyRequest;
  receivedAt: number;
}
const _clarifications = new Map<string, StoredClarify>();

// Bounded per-session memory of clarify ids that can no longer answer
// (dismissed, responded, or resolved) so replay cannot resurrect them.
const _dismissed = new Map<string, string[]>();
const DISMISSED_CAP = 50;

/** Remember a clarify id as no longer answerable for this session. */
function _recordDismissed(sid: string, id: string): void {
  let list = _dismissed.get(sid);
  if (!list) {
    list = [];
    _dismissed.set(sid, list);
  }
  if (list.includes(id)) return;
  list.push(id);
  while (list.length > DISMISSED_CAP) list.shift();
}

/** True when this clarify id can no longer answer for this session. */
function _isDismissed(sid: string, id: string): boolean {
  return (_dismissed.get(sid) ?? []).includes(id);
}

// Current session ID
let _currentSessionId: string | null = null;

// Visible-change callback
let _onVisibleChange: ((visible: boolean) => void) | null = null;

// Internal setters
let _renderClarify: ((clarify: ClarifyRequest | null) => void) | null = null;
let _renderVisible: ((v: boolean) => void) | null = null;

/** Register a callback that fires when card visibility changes. */
export function onClarifyCardVisibleChange(fn: (visible: boolean) => void): () => void {
  _onVisibleChange = fn;
  return () => { _onVisibleChange = null; };
}

/** Notify ClarifyCard of a session change. */
export function setClarifyCardCurrentSessionId(sessionId: string | null) {
  _currentSessionId = sessionId;
  if (sessionId) {
    const stored = _clarifications.get(sessionId);
    if (stored) {
      _renderClarify?.(stored.detail);
      _renderVisible?.(true);
      _onVisibleChange?.(true);
    } else {
      _renderVisible?.(false);
      _onVisibleChange?.(false);
    }
  } else {
    _renderVisible?.(false);
    _onVisibleChange?.(false);
  }
}

// ── Module-level clarify event listener ─────────────────────────────────
// Registered ONCE at module scope so it survives component mount/unmount.
// If the user switches away from the chat view (unmounting ClarifyCard),
// incoming clarifications are still captured and will be restored when
// the user returns.

function _handleClarify(e: CustomEvent) {
  const detail = e.detail as ClarifyRequest;
  const sid = detail._session_id || '';

  // A dismissed/responded/resolved id can never answer again — never
  // re-store or re-show it on replay.
  if (detail.clarify_id && _isDismissed(sid, detail.clarify_id)) return;

  // Same id already stored → duplicate delivery. Leave receivedAt and
  // rendering untouched so the countdown stays anchored to first delivery.
  const existing = _clarifications.get(sid);
  if (existing && detail.clarify_id && existing.detail.clarify_id === detail.clarify_id) {
    return;
  }

  // Store — even if the component is unmounted. A genuinely new id simply
  // replaces the stored entry (no dismissal recorded for the old one).
  _clarifications.set(sid, { detail, receivedAt: Date.now() });

  // Only display if for the current session AND the component is mounted
  if (sid === _currentSessionId && _renderClarify) {
    _renderClarify(detail);
    _renderVisible?.(true);
    _onVisibleChange?.(true);
  }
}

window.addEventListener('clarify-required', _handleClarify as EventListener);

// Dismiss a clarify card as soon as the backend resolves it (responded,
// cancelled, or timed out) so the UI never lingers past its lifecycle.
function _handleResolved(e: CustomEvent) {
  const detail = e.detail as { _session_id?: string; clarify_id?: string; reason?: string };
  const sid = detail._session_id || '';
  const stored = _clarifications.get(sid);
  const matches = stored && (!detail.clarify_id || !stored.detail.clarify_id
    || stored.detail.clarify_id === detail.clarify_id);
  if (detail.clarify_id) _recordDismissed(sid, detail.clarify_id);
  if (!matches) return;
  _clarifications.delete(sid);
  if (sid === _currentSessionId) {
    _renderClarify?.(null);
    _renderVisible?.(false);
    _onVisibleChange?.(false);
    if (detail.reason === 'timeout') {
      showInfoCard(sid, i18n.t('chat:clarify.timeout'));
    }
  }
}
window.addEventListener('clarify-resolved', _handleResolved as EventListener);
// ────────────────────────────────────────────────────────────────────────

export default function ClarifyCard() {
  const { t } = useTranslation('chat');
  const [visible, setVisible] = useState(false);
  const [clarify, setClarify] = useState<ClarifyRequest | null>(null);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    _renderClarify = (c: ClarifyRequest | null) => {
      setClarify(c);
    };
    _renderVisible = (v: boolean) => {
      setVisible(v);
    };

    // Restore any pending clarification that arrived while unmounted
    const stored = _currentSessionId ? _clarifications.get(_currentSessionId) : undefined;
    if (stored && !visible) {
      setClarify(stored.detail);
      setVisible(true);
      _onVisibleChange?.(true);
    }

    return () => {
      _renderClarify = null;
      _renderVisible = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSelectChoice = async (choice: string) => {
    if (!clarify) return;

    setLoading(true);
    try {
      await clarifyService.respond(clarify._session_id || '', choice, clarify.clarify_id);
      if (clarify.clarify_id) _recordDismissed(clarify._session_id || '', clarify.clarify_id);
      _clarifications.delete(clarify._session_id || '');
      setVisible(false);
      setClarify(null);
      _onVisibleChange?.(false);
    } catch (err) {
      alert(`Failed to respond: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async () => {
    if (!clarify || !input.trim()) return;

    setLoading(true);
    try {
      await clarifyService.respond(clarify._session_id || '', input.trim(), clarify.clarify_id);
      if (clarify.clarify_id) _recordDismissed(clarify._session_id || '', clarify.clarify_id);
      _clarifications.delete(clarify._session_id || '');
      setVisible(false);
      setClarify(null);
      setInput('');
      _onVisibleChange?.(false);
    } catch (err) {
      alert(`Failed to respond: ${(err as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // Snapshot the initial timeout so settings changes during streaming
  // don't reset the auto-dismiss timer.
  const clarifyTimeoutSec = useSettingsStore((s) => s.settings?.clarify_timeout_sec);
  const timeoutSec = useMemo(() => {
    const v = parseInt(clarifyTimeoutSec ?? '600', 10);
    return Math.max(1, isNaN(v) ? 600 : v);
  }, [clarifyTimeoutSec]);
  const timeoutMsRef = useRef(timeoutSec * 1000);
  // Guard against double-dismiss (React Strict Mode / concurrent rendering).
  const dismissedRef = useRef(false);

  // Auto-dismiss after timeout — respond with first choice as default.
  // Countdown is based on when the clarification was RECEIVED, not when
  // the card became visible.  timeoutMsRef is a snapshot taken on first
  // render so the timer never resets due to external settings changes.
  useEffect(() => {
    if (!visible || !clarify) {
      return;
    }
    dismissedRef.current = false;

    const sid = clarify._session_id || '';
    const stored = _clarifications.get(sid);
    const elapsed = stored ? Date.now() - stored.receivedAt : 0;
    const remaining = Math.max(0, timeoutMsRef.current - elapsed);

    const dismiss = () => {
      if (dismissedRef.current) {
        return;
      }
      dismissedRef.current = true;
      const sessionId = sid || _currentSessionId || '';
      if (clarify.clarify_id) _recordDismissed(sid, clarify.clarify_id);
      _clarifications.delete(sid);
      setVisible(false);
      setClarify(null);
      _onVisibleChange?.(false);
      showInfoCard(sessionId, t('clarify.timeout'));
    };

    if (remaining <= 0) {
      dismiss();
      return;
    }

    const timer = setTimeout(dismiss, remaining);
    return () => {
      clearTimeout(timer);
    };
  }, [visible, clarify]);

  if (!visible || !clarify) return null;

  const choices = clarify.choices || clarify.choices_offered || [];

  return (
    <div className={`bg-task-card ${visible ? 'visible' : ''}`}>
      <div className="bg-task-inner">
        <div className="bg-task-content">
          <div className="bg-task-desc">
            {clarify.question && (
              <div style={{ fontWeight: 500, marginBottom: 8 }}>{clarify.question}</div>
            )}

            {choices.length > 0 && (
              <div className="bg-task-btns" style={{ flexDirection: 'column', gap: 6, marginBottom: 8 }}>
                {choices.map((choice, index) => (
                  <button
                    key={index}
                    className="bg-task-btn"
                    onClick={() => handleSelectChoice(choice)}
                    disabled={loading}
                    style={{ textAlign: 'left' }}
                  >
                    <span style={{ opacity: 0.5, marginRight: 6 }}>{index + 1}.</span>
                    {choice}
                  </button>
                ))}
              </div>
            )}

            <div style={{ display: 'flex', gap: 6 }}>
              <input
                type="text"
                className="bg-task-btn"
                style={{ flex: 1, padding: '5px 10px', border: '1px solid var(--border)', borderRadius: 8, background: 'transparent', color: 'var(--text)', fontSize: 14, outline: 'none' }}
                placeholder={t('clarify.other')}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={loading}
              />
            </div>
          </div>
          <div className="bg-task-sider">
            <button className="bg-task-btn bg-task-btn--primary" onClick={handleSubmit} disabled={loading || !input.trim()}>
              {loading ? '...' : t('clarify.submit')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
