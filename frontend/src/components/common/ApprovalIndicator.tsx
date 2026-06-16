import { useState, useRef, useEffect } from 'react';
import { useApprovalStore } from '@/stores/useApprovalStore';
import { useSessionStore } from '@/stores/useSessionStore';

export default function ApprovalIndicator() {
  const { sessionApproved, permanentApproved, loadApproved, revokeApproved } = useApprovalStore();
  const { currentSession } = useSessionStore();
  const sessionId = currentSession?.session_id ?? null;
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Load approved patterns when session changes
  useEffect(() => {
    if (sessionId) {
      loadApproved(sessionId);
    }
  }, [sessionId, loadApproved]);

  // Dismiss on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

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
        className={`approval-indicator-chip${hasAny ? ' has-approvals' : ''}`}
        onClick={() => setOpen((v) => !v)}
        title="Manage session approvals"
        aria-label="Manage session approvals"
      >
        <svg width="10" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        </svg>
        {totalSession > 0 && (
          <span className="approval-indicator-count">{totalSession}</span>
        )}
      </button>

      {open && (
        <div className="approval-popover">
          <div className="approval-popover-header">
            Session Approvals
            {totalSession > 0 && <span className="approval-popover-badge">{totalSession}</span>}
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
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
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
