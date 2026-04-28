import { useState } from 'react';
import type { ToolCall } from '@/types';

interface ToolCardProps {
  toolCall: ToolCall;
}

export default function ToolCard({ toolCall }: ToolCardProps) {
  const [expanded, setExpanded] = useState(false);

  const getStatusIcon = () => {
    if (toolCall.is_error) {
      return (
        <svg className="text-[var(--accent)]" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10" />
          <line x1="15" y1="9" x2="9" y2="15" />
          <line x1="9" y1="9" x2="15" y2="15" />
        </svg>
      );
    }
    if (toolCall.done) {
      return (
        <svg className="text-[var(--blue)]" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="20 6 9 17 4 12" />
        </svg>
      );
    }
    return (
      <svg className="animate-spin text-[var(--blue)]" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
        <path d="M12 2a10 10 0 0 1 10 10" />
      </svg>
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
    <div className={`tool-card ${toolCall.is_error ? 'error' : ''}`}>
      <div className="tool-card-header" onClick={() => setExpanded(!expanded)}>
        {getStatusIcon()}
        <span className="tool-card-name">{toolCall.name}</span>
        <span className="tool-card-preview">{getPreviewText()}</span>
        {!toolCall.done && (
          <svg className="ml-auto animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
            <path d="M12 2a10 10 0 0 1 10 10" />
          </svg>
        )}
        {toolCall.duration !== undefined && (
          <span className="ml-auto text-xs text-[var(--muted)]">{toolCall.duration}ms</span>
        )}
      </div>
      
      {expanded && (
        <div className="tool-card-body">
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
        </div>
      )}
    </div>
  );
}
