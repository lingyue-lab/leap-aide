import { useState } from 'react';
import type { ReactNode } from 'react';
import { ChevronRightIcon } from '@/components/common/Icons';

export type CollapseTone = 'tool' | 'reasoning' | 'context' | 'error' | 'steered';

export interface CollapseCardProps {
  /** Leading icon (status / reasoning / star / spinner). */
  icon?: ReactNode;
  /** Header title (tool name / "reasoning" / "context compaction"). */
  title: ReactNode;
  /** Secondary preview text shown after the title. */
  snippet?: ReactNode;
  /** Extra header content (duration, steer badge, spinner, time). */
  extra?: ReactNode;
  /** Color variant; defaults to 'tool'. */
  tone?: CollapseTone;
  defaultExpanded?: boolean;
  /** Extra classes on the card container. */
  className?: string;
  /** Expandable body (optional — cards with no body render header only). */
  children?: ReactNode;
}

/**
 * Unified collapsible card used by tool / reasoning / context-compaction rows.
 * Header + optional expandable body, tone-tinted per Material Design 3.
 */
export default function CollapseCard({
  icon,
  title,
  snippet,
  extra,
  tone = 'tool',
  defaultExpanded = false,
  className,
  children,
}: CollapseCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  return (
    <div className={`collapse-card collapse-card--${tone}${className ? ` ${className}` : ''}`}>
      <button
        type="button"
        className="collapse-card-header"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        {icon && <span className="collapse-card-icon">{icon}</span>}
        <span className="collapse-card-title">{title}</span>
        {extra}
        {snippet && <span className="collapse-card-snippet">{snippet}</span>}
        <span className="collapse-card-spacer" />
        <ChevronRightIcon className={`collapse-card-chevron${expanded ? ' is-expanded' : ''}`} />
      </button>
      {expanded && children != null && <div className="collapse-card-body">{children}</div>}
    </div>
  );
}
