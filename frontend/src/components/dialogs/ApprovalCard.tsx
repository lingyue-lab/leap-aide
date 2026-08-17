import { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useModeStore } from '@/stores/useModeStore';
import { useApprovalStore } from '@/stores/useApprovalStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { approvalService } from '@/services/api';
import type { ApprovalRequest } from '@/types';

// Per-session Map for approval state storage.
// Keys are session IDs, values include the approval detail plus a receivedAt
// timestamp so the auto-deny timeout can be computed from receipt time, not
// from when the card became visible (which may be much later if the user
// switched away and back).
interface StoredApproval {
  detail: ApprovalRequest;
  receivedAt: number;
}
const _approvals = new Map<string, StoredApproval>();

// Current session ID
let _currentSessionId: string | null = null;

// Visible-change callback
let _onVisibleChange: ((visible: boolean) => void) | null = null;

// Internal setters — set by the React component on mount, cleared on unmount
let _renderApproval: ((approval: ApprovalRequest | null) => void) | null = null;
let _renderVisible: ((v: boolean) => void) | null = null;

/** Register a callback that fires when card visibility changes. */
export function onApprovalCardVisibleChange(fn: (visible: boolean) => void): () => void {
  _onVisibleChange = fn;
  return () => { _onVisibleChange = null; };
}

/** Notify ApprovalCard of a session change. */
export function setApprovalCardCurrentSessionId(sessionId: string | null) {
  _currentSessionId = sessionId;
  if (sessionId) {
    const stored = _approvals.get(sessionId);
    if (stored) {
      _renderApproval?.(stored.detail);
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

// ── Module-level approval event listener ────────────────────────────────
// Registered ONCE at module scope so it survives component mount/unmount.
// If the user switches away from the chat view (unmounting ApprovalCard),
// incoming approvals are still captured into _approvals and will be
// restored when the user returns.

function _handleApproval(e: CustomEvent) {
  const currentMode = useModeStore.getState().currentMode;
  const detail = e.detail as ApprovalRequest;
  const sid = detail._session_id || '';

  // Always store — even if we can't display it right now
  _approvals.set(sid, { detail, receivedAt: Date.now() });

  // YOLO mode: auto-approve without showing the card
  if (currentMode === 'yolo') {
    if (sid) {
      approvalService.respond(sid, 'session', detail.approval_id).catch(() => {});
    }
    return;
  }

  // Only display if this approval is for the current session AND the
  // component is mounted (_renderApproval is non-null).
  if (sid === _currentSessionId && _renderApproval) {
    _renderApproval(detail);
    _renderVisible?.(true);
    _onVisibleChange?.(true);
  }
}

window.addEventListener('approval-required', _handleApproval as EventListener);
// ────────────────────────────────────────────────────────────────────────

export default function ApprovalCard() {
  const { t } = useTranslation('chat');
  const [visible, setVisible] = useState(false);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  // Wire React state setters into the module-level singletons.
  // On mount, also check if there's a pending approval for the current
  // session that arrived while the component was unmounted.
  useEffect(() => {
    _renderApproval = (a: ApprovalRequest | null) => {
      setApproval(a);
    };
    _renderVisible = (v: boolean) => {
      setVisible(v);
    };

    // Restore any pending approval that arrived while we were unmounted
    const stored = _currentSessionId ? _approvals.get(_currentSessionId) : undefined;
    if (stored && !visible) {
      setApproval(stored.detail);
      setVisible(true);
      _onVisibleChange?.(true);
    }

    return () => {
      _renderApproval = null;
      _renderVisible = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRespond = async (choice: 'once' | 'session' | 'deny') => {
    if (!approval) return;

    setLoading(choice);
    try {
      await approvalService.respond(approval._session_id || '', choice, approval.approval_id);
      if (choice === 'session') {
        const patterns = approval.pattern_keys || (approval.pattern_key ? [approval.pattern_key] : []);
        if (patterns.length > 0) {
          useApprovalStore.getState().addApproved(patterns);
        }
      }
      _approvals.delete(approval._session_id || '');
      setVisible(false);
      setApproval(null);
      _onVisibleChange?.(false);
    } catch (err) {
      alert(`Failed to respond: ${(err as Error).message}`);
    } finally {
      setLoading(null);
    }
  };

  const approvalTimeoutSec = useSettingsStore((s) => s.settings?.approval_timeout_sec);
  const timeoutSec = useMemo(() => {
    const v = parseInt(approvalTimeoutSec ?? '60', 10);
    return Math.max(1, isNaN(v) ? 60 : v);
  }, [approvalTimeoutSec]);

  // Auto-dismiss after timeout.
  // The countdown is based on when the approval was RECEIVED (receivedAt),
  // not when the card became visible.  This prevents the card from showing
  // a full timeout period when the user returns to a session long after the
  // approval was sent.
  useEffect(() => {
    if (!visible || !approval) return;

    const sid = approval._session_id || '';
    const stored = _approvals.get(sid);
    const elapsed = stored ? Date.now() - stored.receivedAt : 0;
    const remaining = Math.max(0, timeoutSec * 1000 - elapsed);

    if (remaining <= 0) {
      // Already expired — deny immediately without flashing the card
      handleRespond('deny');
      return;
    }

    const timer = setTimeout(() => {
      handleRespond('deny');
    }, remaining);
    return () => clearTimeout(timer);
  }, [visible, approval, handleRespond, timeoutSec]);

  if (!visible || !approval) return null;

  return (
    <div className={`bg-task-card ${visible ? 'visible' : ''}`}>
      <div className="bg-task-inner">
        <div className="bg-task-content">
          <div className="bg-task-desc">
            <span>{t('approval.title')}</span>
            {approval.command && (
              <code style={{ fontSize: 13, wordBreak: 'break-all', marginTop: 4, display: 'block' }}>{approval.command}</code>
            )}
          </div>
          <div className="bg-task-sider">
            <div className="bg-task-btns" style={{ flexDirection: 'column' }}>
              <button className="bg-task-btn" onClick={() => handleRespond('once')} disabled={!!loading}>
                {loading === 'once' ? '...' : t('approval.once')}
              </button>
              <button className="bg-task-btn" onClick={() => handleRespond('session')} disabled={!!loading}>
                {loading === 'session' ? '...' : t('approval.session')}
              </button>
              <button className="bg-task-btn" onClick={() => handleRespond('deny')} disabled={!!loading}>
                {loading === 'deny' ? '...' : t('approval.deny')}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
