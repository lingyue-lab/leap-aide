import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '@/stores/useChatStore';
import { approvalService } from '@/services/api';
import type { ApprovalRequest } from '@/types';

export default function ApprovalCard() {
  const { t } = useTranslation('chat');
  const { busy, activeStreamId } = useChatStore();
  const [visible, setVisible] = useState(false);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  useEffect(() => {
    const handleApproval = (e: CustomEvent) => {
      setApproval(e.detail);
      setVisible(true);
    };

    window.addEventListener('approval-required', handleApproval as EventListener);
    return () => {
      window.removeEventListener('approval-required', handleApproval as EventListener);
    };
  }, []);

  useEffect(() => {
    if (!activeStreamId || !busy) {
      setVisible(false);
      setApproval(null);
    }
  }, [activeStreamId, busy]);

  const handleRespond = async (choice: 'once' | 'session' | 'always' | 'deny') => {
    if (!approval) return;
    
    setLoading(choice);
    try {
      await approvalService.respond(approval._session_id || '', choice, approval.approval_id);
      setVisible(false);
      setApproval(null);
    } catch (err) {
      alert(`Failed to respond: ${(err as Error).message}`);
    } finally {
      setLoading(null);
    }
  };

  if (!visible) return null;

  return (
    <div className={`approval-card ${visible ? 'visible' : ''}`}>
      <div className="approval-inner">
        <div className="approval-header">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          {t('approval.title')}
        </div>
        
        {approval?.description && (
          <div className="approval-desc">{approval.description}</div>
        )}
        
        {approval?.command && (
          <div className="approval-cmd">{approval.command}</div>
        )}
        
        <div className="approval-btns">
          <button
            className="approval-btn once"
            onClick={() => handleRespond('once')}
            disabled={loading !== null}
          >
            {t('approval.once')}
          </button>
          <button
            className="approval-btn session"
            onClick={() => handleRespond('session')}
            disabled={loading !== null}
          >
            {t('approval.session')}
          </button>
          <button
            className="approval-btn always"
            onClick={() => handleRespond('always')}
            disabled={loading !== null}
          >
            {t('approval.always')}
          </button>
          <button
            className="approval-btn deny"
            onClick={() => handleRespond('deny')}
            disabled={loading !== null}
          >
            {t('approval.deny')}
          </button>
        </div>
      </div>
    </div>
  );
}
