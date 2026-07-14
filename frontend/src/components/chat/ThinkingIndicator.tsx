import { useTranslation } from 'react-i18next';

interface ThinkingIndicatorProps {
  phase?: string | null;
}

function getPhaseLabelKey(phase: string | null | undefined): string {
  switch (phase) {
    case 'connecting':
      return 'connecting';
    case 'initializing':
      return 'initializing';
    case 'thinking':
      return 'thinking_phase';
    case 'running':
      return 'running';
    default:
      return 'thinking';
  }
}

export default function ThinkingIndicator({ phase }: ThinkingIndicatorProps) {
  const { t } = useTranslation('chat');
  const labelKey = getPhaseLabelKey(phase);
  const isRunning = phase === 'running';

  return (
    <div className={`thinking-indicator${isRunning ? ' thinking-indicator--running' : ''}`}>
      <div className="thinking">
        {isRunning ? (
          <span className="thinking-running-icon">◉</span>
        ) : (
          <>
            <span className="thinking-dot" />
            <span className="thinking-dot" />
            <span className="thinking-dot" />
          </>
        )}
        <span className="ml-2">{t(labelKey)}</span>
      </div>
    </div>
  );
}
