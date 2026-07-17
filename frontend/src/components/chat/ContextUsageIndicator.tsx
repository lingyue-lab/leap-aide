import React, { useMemo, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import type { UsageInfo, Session } from '../../types';
import { sessionService } from '../../services/api';
import { useSessionStore } from '../../stores/useSessionStore';
import { useChatStore } from '../../stores/useChatStore';

// Hardcoded colour thresholds (v1, not user-configurable)
const THRESHOLD_WARN = 0.60;   // Green → Yellow at 60%
const THRESHOLD_DANGER = 0.85; // Yellow → Red at 85%

// SVG sizing constants
const SIZE = 30;
const STROKE_WIDTH = 3;
const RADIUS = (SIZE - STROKE_WIDTH) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

interface ContextUsageIndicatorProps {
  usage: UsageInfo | null;
  session?: Session | null;
  lastCompressionTime?: number | null;
  streaming?: boolean;
  onCompressingChange?: (compressing: boolean) => void;
}

function getColour(ratio: number): string {
  if (ratio >= THRESHOLD_DANGER) return '#ef4444';   // red-500
  if (ratio >= THRESHOLD_WARN) return '#eab308';     // yellow-500
  return '#22c55e';                                   // green-500
}

function fmtTokens(n: number | undefined | null): string {
  if (n == null || n <= 0) return '?';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

function fmtTime(ts: number | undefined | null): string {
  if (ts == null || ts <= 0) return '?';
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ── Tooltip popover ──────────────────────────────────────────────────────

interface TooltipLine {
  label: string;
  value: string;
}

const Tooltip: React.FC<{
  lines: TooltipLine[];
  visible: boolean;
  footer: React.ReactNode;
}> = ({ lines, visible, footer }) => {
  if (!visible) return null;
  return (
    <div className="context-usage-tooltip">
      {lines.map((line, i) => (
        <div key={i} className="context-usage-tooltip-line">
          <span className="context-usage-tooltip-label">{line.label}</span>
          <span className="context-usage-tooltip-value">{line.value}</span>
        </div>
      ))}
      <div className="context-usage-tooltip-separator" />
      <div className="context-usage-tooltip-footer">{footer}</div>
    </div>
  );
};

// ── Main component ───────────────────────────────────────────────────────

const ContextUsageIndicator: React.FC<ContextUsageIndicatorProps> = ({
  usage,
  session,
  lastCompressionTime,
  streaming = false,
  onCompressingChange,
}) => {
  const { t } = useTranslation();
  const loadSession = useSessionStore((s) => s.loadSession);
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const [tooltipVisible, setTooltipVisible] = useState(false);
  const [compressing, setCompressing] = useState(false);

  const handleMouseEnter = useCallback(() => {
    setTooltipVisible(true);
  }, []);

  const handleMouseLeave = useCallback(() => {
    setTooltipVisible(false);
  }, []);

  const handleClick = useCallback(async () => {
    if (streaming || compressing) return;
    const sid = session?.session_id;
    if (!sid) return;
    setCompressing(true);
    onCompressingChange?.(true);
    try {
      const result = await sessionService.compressSession(sid);
      // Update the chat store directly — ChatInterface only syncs
      // messages on sessionId change, but compress stays on the same session.
      if (result?.session?.messages) {
        useChatStore.getState().setMessages(result.session.messages);
        useChatStore.getState().clearCache(sid);
      }
      await loadSession(sid);
      // Refresh sidebar session list so new compaction snapshot appears
      await loadSessions();
    } catch {
      // silently ignore — compression is best-effort
    } finally {
      setCompressing(false);
      onCompressingChange?.(false);
    }
  }, [streaming, compressing, session, loadSession, loadSessions, onCompressingChange]);

  // Derive metrics from usage (live compressor data) first,
  // fall back to persisted session data for old sessions.
  const ctxLen = usage?.context_length || session?.context_length || 0;
  const used = usage?.last_prompt_tokens || session?.last_prompt_tokens || 0;
  const threshold =
    usage?.threshold_tokens || session?.threshold_tokens || 0;
  const hasCtx = ctxLen > 0;
  const hasUsed = used > 0;
  const ratio = hasCtx && hasUsed ? Math.min(used / ctxLen, 1.0) : 0;
  const pct = Math.round(ratio * 100);
  const colour = hasCtx && hasUsed ? getColour(ratio) : '#9ca3af';

  const dashOffset = CIRCUMFERENCE * (1 - ratio);

  // ── Tooltip lines — always all 7 items, "?" when unavailable ──────────

  const tooltipLines: TooltipLine[] = useMemo(() => {
    return [
      {
        label: t('context_usage.context'),
        value: hasCtx && hasUsed ? `${pct}% used` : '?',
      },
      {
        label: t('context_usage.prompt_tokens'),
        value:
          hasUsed && hasCtx
            ? `${fmtTokens(used)} / ${fmtTokens(ctxLen)}`
            : `${fmtTokens(used)} / ${fmtTokens(ctxLen)}`,
      },
      {
        label: t('context_usage.auto_compress_at'),
        value: threshold > 0 ? fmtTokens(threshold) : '?',
      },
      {
        label: t('context_usage.last_compression'),
        value: fmtTime(lastCompressionTime),
      },
      {
        label: t('context_usage.total_input_tokens'),
        value: fmtTokens(session?.input_tokens),
      },
      {
        label: t('context_usage.total_output_tokens'),
        value: fmtTokens(session?.output_tokens),
      },
      {
        label: t('context_usage.messages'),
        value: String(session?.message_count ?? session?.messages?.length ?? '?'),
      },
    ];
  }, [hasCtx, hasUsed, pct, used, ctxLen, threshold, lastCompressionTime, session, t]);

  const ringLabel = hasCtx && hasUsed ? (pct >= 100 ? '!' : `${pct}`) : '?';

  const footerText = streaming
    ? t('context_usage.click_disabled_streaming')
    : compressing
      ? t('context_usage.compressing')
      : t('context_usage.click_to_compress');

  const canClick = !streaming && !compressing && !!session?.session_id;
  const autoCompressDisabled = (session as any)?.auto_compress_disabled === true;

  return (
    <div
      className={`context-usage-indicator${canClick ? ' context-usage-clickable' : ''}`}
      title={autoCompressDisabled ? 'Auto-compress disabled due to poor compression ratio. Clear history to re-enable.' : undefined}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onClick={handleClick}
    >
      <svg
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        className="context-usage-ring"
      >
        {/* Background circle */}
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          stroke="currentColor"
          strokeWidth={STROKE_WIDTH}
          className="context-usage-ring-bg"
          opacity={0.2}
        />
        {/* Progress arc — only when we have meaningful data */}
        {hasCtx && hasUsed && ratio > 0 && (
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke={colour}
            strokeWidth={STROKE_WIDTH}
            strokeLinecap="round"
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={dashOffset}
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
            className="context-usage-ring-fg"
            style={{ transition: 'stroke-dashoffset 0.5s ease, stroke 0.5s ease' }}
          />
        )}
        {/* Center text */}
        <text
          x={SIZE / 2}
          y={SIZE / 2}
          textAnchor="middle"
          dominantBaseline="central"
          className="context-usage-ring-text"
          fontSize={hasCtx && hasUsed ? SIZE * 0.32 : SIZE * 0.36}
          fill={hasCtx && hasUsed ? colour : 'currentColor'}
        >
          {ringLabel}
        </text>
      </svg>
      <Tooltip lines={tooltipLines} visible={tooltipVisible} footer={footerText} />
    </div>
  );
};

export default ContextUsageIndicator;
