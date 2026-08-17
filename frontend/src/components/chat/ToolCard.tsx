import type { ToolCall } from '@/types';
import CollapseCard from './CollapseCard';
import { Icon } from '@/components/common/Icons';

interface ToolCardProps {
  toolCall: ToolCall;
}

export default function ToolCard({ toolCall }: ToolCardProps) {
  const getStatusIcon = () => {
    if (toolCall.is_error) {
      return (
        <Icon name="cancel" size={18} className="text-[var(--accent)]" />
      );
    }
    if (toolCall.done) {
      return (
        <Icon name="check" size={18} className="text-[var(--blue)]" />
      );
    }
    return (
      <Icon name="progress_activity" size={18} className="animate-spin text-[var(--blue)]" />
    );
  };

  const getPreviewText = () => {
    if (toolCall.preview) {
      return toolCall.preview;
    }
    if (toolCall.args && Object.keys(toolCall.args).length > 0) {
      try {
        return JSON.stringify(toolCall.args, null, 2).slice(0, 100);
      } catch {
        return '';
      }
    }
    return '';
  };

  return (
    <CollapseCard
      tone={toolCall.is_error ? 'error' : 'tool'}
      title={toolCall.name}
      icon={getStatusIcon()}
      snippet={getPreviewText()}
      extra={
        <>
          {!toolCall.done && (
            <Icon name="progress_activity" size={18} className="animate-spin" />
          )}
          {toolCall.duration !== undefined && (
            <span className="text-xs text-[var(--muted)]">{toolCall.duration}ms</span>
          )}
        </>
      }
    >
      {toolCall.snippet && (
        <pre className="text-xs overflow-auto max-h-48 p-2 bg-[var(--code-bg)] rounded">
          {toolCall.snippet}
        </pre>
      )}
      {toolCall.args && Object.keys(toolCall.args).length > 0 && (
        <pre className="text-xs overflow-auto max-h-48 p-2 bg-[var(--code-bg)] rounded mt-2">
          {JSON.stringify(toolCall.args, null, 2)}
        </pre>
      )}
    </CollapseCard>
  );
}
