import React, { useState } from 'react';
import type { CompressionRecord, Message } from '../../types';

interface CompressionCardProps {
  record: CompressionRecord;
  collapseMode?: 'single' | 'summary' | 'hidden';
  totalCount?: number;
  onExpand?: () => void;
}

/** Check if a message carries compression metadata. */
export function isCompressionMessage(msg: Message): boolean {
  return msg._compression != null && msg._compression.trigger != null;
}

/** Extract CompressionRecord from a message, or null. */
export function getCompressionRecord(msg: Message): CompressionRecord | null {
  return msg._compression ?? null;
}

/** Format approximate token count with ≈ prefix. */
function fmtTokens(n: number): string {
  if (n >= 1000) return `≈${Math.round(n / 1000)}K`;
  return `≈${n}`;
}

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

const CompressionCard: React.FC<CompressionCardProps> = ({
  record,
  collapseMode = 'single',
  totalCount,
  onExpand,
}) => {
  const [expanded, setExpanded] = useState(collapseMode !== 'summary');

  const saved = record.messages_before - record.messages_after;
  const savedTokens = record.approx_tokens_before - record.approx_tokens_after;
  const isAuto = record.trigger === 'auto';

  if (collapseMode === 'summary') {
    return (
      <div className="compression-card compression-card-summary" onClick={onExpand}>
        <div className="compression-card-summary-text">
          {totalCount != null ? `${totalCount} compressions` : ''} — click to expand
        </div>
      </div>
    );
  }

  return (
    <div className={`compression-card ${isAuto ? 'compression-card-auto' : 'compression-card-manual'}`}>
      <div className="compression-card-header" onClick={() => setExpanded(!expanded)}>
        <span className="compression-card-icon">{isAuto ? '⚡' : '📦'}</span>
        <span className="compression-card-label">
          {isAuto ? 'Auto-compressed' : 'Manual compression'}
        </span>
        <span className="compression-card-stats">
          {saved} msg saved · {fmtTokens(savedTokens)} tokens
        </span>
        <span className="compression-card-time">{formatTime(record.compression_time)}</span>
        <span className="compression-card-caret">{expanded ? '▾' : '▸'}</span>
      </div>
      {expanded && record.summary_text && (
        <div className="compression-card-body">
          <div className="compression-card-summary">
            {record.summary_text}
          </div>
        </div>
      )}
      {expanded && (
        <div className="compression-card-details">
          <span>Before: {record.messages_before} msgs ({fmtTokens(record.approx_tokens_before)} tok)</span>
          <span className="compression-card-arrow">→</span>
          <span>After: {record.messages_after} msgs ({fmtTokens(record.approx_tokens_after)} tok)</span>
        </div>
      )}
    </div>
  );
};

export default CompressionCard;
