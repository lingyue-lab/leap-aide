import { useChatStore } from '@/stores/useChatStore';
import { chatService } from '@/services/api';
import { useTranslation } from 'react-i18next';

interface QueueStatusBarProps {
  sessionId: string | null;
}

export default function QueueStatusBar({ sessionId }: QueueStatusBarProps) {
  const { t } = useTranslation('common');
  const queuedMessages = useChatStore((s) => s.queuedMessages);
  const clearQueuedMessages = useChatStore((s) => s.clearQueuedMessages);

  // Debug: log every render to see what value Zustand gives us

  if (queuedMessages.length === 0) return null;

  const handleCancelAll = async () => {
    if (sessionId) {
      try {
        await chatService.clearQueue(sessionId);
      } catch {
        // Silently ignore — queue is in-memory anyway
      }
    }
    clearQueuedMessages();
  };

  return (
    <div className="queue-status-bar">
      <div className="queue-status-left">
        <span className="queue-status-icon">📋</span>
        <span className="queue-status-text">
          {t('chat.queue_waiting', { n: queuedMessages.length })}
        </span>
        <span className="queue-status-divider">·</span>
        <span className="queue-status-hint">{t('chat.queue_hint')}</span>
      </div>
      <button
        className="queue-status-cancel-btn"
        onClick={handleCancelAll}
        title={t('chat.queue_cancel_all')}
      >
        {t('chat.queue_cancel_all')}
      </button>
    </div>
  );
}
