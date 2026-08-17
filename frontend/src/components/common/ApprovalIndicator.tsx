import { useState, useRef, useEffect, useCallback } from 'react';
import { useApprovalStore } from '@/stores/useApprovalStore';
import { useSessionStore } from '@/stores/useSessionStore';
import { Icon } from '@/components/common/Icons';

export default function ApprovalIndicator() {
  const { sessionApproved, permanentApproved, loadApproved, revokeApproved } = useApprovalStore();
  const { currentSession } = useSessionStore();
  const sessionId = currentSession?.session_id ?? null;
  const [open, setOpen] = useState(false);
  const [popoverStyle, setPopoverStyle] = useState<React.CSSProperties>({});
  const containerRef = useRef<HTMLDivElement>(null);

  // Load approved patterns when session changes
  useEffect(() => {
    if (sessionId) {
      loadApproved(sessionId);
    }
  }, [sessionId, loadApproved]);

  const close = useCallback(() => setOpen(false), []);

  const toggle = useCallback(() => {
    setOpen((v) => {
      if (!v && containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        const POPOVER_W = 280;
        setPopoverStyle({
          position: 'fixed',
          top: rect.top - 8,
          left: Math.max(8, rect.left + rect.width / 2 - POPOVER_W / 2),
          minWidth: POPOVER_W,
          maxWidth: 360,
          transform: 'translateY(-100%)',
        });
      }
      return !v;
    });
  }, []);

  const handleRevoke = async (patternKey: string) => {
    if (!sessionId) return;
    await revokeApproved(sessionId, patternKey);
    // Re-sync from backend to ensure UI matches persisted state
    loadApproved(sessionId);
  };

  const totalSession = sessionApproved.length;
  const hasAny = totalSession > 0 || permanentApproved.length > 0;

  return (
    <div className="approval-indicator" ref={containerRef}>
      <button
        className={`icon-btn${hasAny ? ' has-approvals' : ''}`}
        onClick={toggle}
        title="Manage session approvals"
        aria-label="Manage session approvals"
      >
        <Icon name="shield" size={18} style={{ flexShrink: 0 }} />
      </button>

      {open && (
        <div className="approval-popover" style={popoverStyle}>
          <div className="ss-topbar">
            <div className="approval-popover-header">
              Session Approvals
              {totalSession > 0 && <span className="approval-popover-badge">{totalSession}</span>}
            </div>
            <Icon name="close" size={18} className="ss-close" onClick={close} />
          </div>

          {totalSession > 0 ? (
            <div className="approval-popover-list">
              {sessionApproved.map((pattern) => (
                <div key={pattern} className="approval-popover-item">
                  <span className="approval-popover-item-text">{pattern}</span>
                  <button
                    className="approval-revoke-btn"
                    onClick={() => handleRevoke(pattern)}
                    title={`Revoke approval for "${pattern}"`}
                    aria-label={`Revoke "${pattern}"`}
                  >
                    <Icon name="close" size={18} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="approval-popover-empty">No session approvals</div>
          )}

          {permanentApproved.length > 0 && (
            <>
              <div className="approval-popover-divider" />
              <div className="approval-popover-header approval-popover-header-permanent">
                Permanent Allowlist
              </div>
              <div className="approval-popover-list">
                {permanentApproved.map((pattern) => (
                  <div key={pattern} className="approval-popover-item approval-popover-item-permanent">
                    <span className="approval-popover-item-text">{pattern}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
