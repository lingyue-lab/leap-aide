import { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import type { Message } from '@/types';

interface MinimapItem {
  idx: number;
  text: string;
  isActive: boolean;
}

interface ConversationMinimapProps {
  messages: Message[];
  containerRef: React.RefObject<HTMLDivElement | null>;
}

/** Strip common markdown formatting for compact preview text. */
function stripMarkdown(text: string): string {
  if (!text) return '';
  return text
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/_(.+?)_/g, '$1')
    .replace(/`{1,3}[^`]*`{1,3}/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/!\[.*?\]\([^)]+\)/g, '')
    .replace(/>\s+/gm, '')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/^\d+\.\s+/gm, '')
    .replace(/~~~?[\s\S]*?~~~/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/\\/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .substring(0, 60);
}

export default function ConversationMinimap({ messages, containerRef }: ConversationMinimapProps) {
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);
  const railRef = useRef<HTMLDivElement>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isClickNavigating = useRef(false);

  // Extract user messages with their original indices.
  // Include ALL user messages (even empty) so markers stay 1:1 with actual messages.
  const userMarkers: MinimapItem[] = useMemo(() => {
    const markers: MinimapItem[] = [];
    let seq = 0;
    messages.forEach((m, i) => {
      if (m.role !== 'user') return;
      seq++;
      const rawContent = typeof m.content === 'string' ? m.content : '';
      // Prefer original_content__webui so skill-invocation expanded text
      // (which includes [IMPORTANT...] activation notes) doesn't leak into
      // the minimap tooltip — matches MessageRow display behaviour.
      const displayContent = (m.original_content__webui as string) || rawContent;
      const text = stripMarkdown(displayContent) || displayContent.substring(0, 60) || `#${seq}`;
      markers.push({ idx: i, text, isActive: i === activeIdx });
    });
    return markers;
  }, [messages, activeIdx]);

  // Track which user message is closest to the top of the scroll container.
  // Strategy: IntersectionObserver (threshold 0) fires on any visibility change
  // during scrolling. However, after a smooth scroll animation settles, the
  // observer may not fire again if no element crosses the 0→>0 threshold at
  // the final position. We add a debounced scroll listener as a backstop that
  // corrects the highlight when scrolling definitively stops.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const msgRows = el.querySelectorAll('[data-user-msg-idx]') as NodeListOf<HTMLElement>;
    if (msgRows.length === 0) return;

    const MIN_VISIBLE_RATIO = 0.1;

    const findFirstVisible = (): number | null => {
      const containerRect = el.getBoundingClientRect();
      let bestIdx: number | null = null;
      let bestTop = Infinity;
      msgRows.forEach((row) => {
        const rowRect = row.getBoundingClientRect();
        const overlapTop = Math.max(rowRect.top, containerRect.top);
        const overlapBottom = Math.min(rowRect.bottom, containerRect.bottom);
        const overlapHeight = overlapBottom - overlapTop;
        if (overlapHeight <= 0) return;
        const visibleRatio = overlapHeight / rowRect.height;
        if (visibleRatio < MIN_VISIBLE_RATIO) return;
        if (rowRect.top < bestTop) {
          bestTop = rowRect.top;
          bestIdx = parseInt(row.dataset.userMsgIdx || '0', 10);
        }
      });
      return bestIdx;
    };

    const updateActive = () => {
      // Suppress observer-driven updates while a click-initiated smooth
      // scroll is in progress — avoids highlight flickering between the
      // clicked target and intermediate messages.
      if (isClickNavigating.current) return;
      const firstIdx = findFirstVisible();
      if (firstIdx !== null) {
        setActiveIdx(firstIdx);
      }
    };

    // Observer fires on every visibility change — keeps highlight live during scroll.
    const observer = new IntersectionObserver(
      () => updateActive(),
      { root: el, threshold: 0 }
    );

    // Debounced scroll listener — fires when scrolling *stops*,
    // correcting the highlight if the observer missed the final frame.
    let scrollTimer: ReturnType<typeof setTimeout> | null = null;
    const SCROLL_DEBOUNCE_MS = 150;

    const handleScroll = () => {
      if (scrollTimer) clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        scrollTimer = null;
        // Scroll has definitively stopped — release any click-navigation
        // lock and run a final correction so the highlight matches the
        // actual first-visible message.
        isClickNavigating.current = false;
        const firstIdx = findFirstVisible();
        if (firstIdx !== null) {
          setActiveIdx(firstIdx);
        }
      }, SCROLL_DEBOUNCE_MS);
    };

    el.addEventListener('scroll', handleScroll, { passive: true });
    msgRows.forEach((row) => observer.observe(row));

    return () => {
      observer.disconnect();
      el.removeEventListener('scroll', handleScroll);
      if (scrollTimer) clearTimeout(scrollTimer);
    };
  }, [containerRef, messages.length]);

  const handleMarkerClick = useCallback(
    (msgIdx: number) => {
      // Lock the highlight to the clicked target during the smooth scroll
      // so intermediate IntersectionObserver firings don't cause flicker.
      isClickNavigating.current = true;
      setActiveIdx(msgIdx);
      const el = containerRef.current;
      if (!el) return;
      const row = el.querySelector(`[data-msg-idx="${msgIdx}"]`) as HTMLElement | null;
      if (row) {
        row.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    },
    [containerRef]
  );

  // Dynamic gap between markers: comfortable spacing when few messages,
  // progressively tighter when many messages need to fit within 55vh.
  const markerStyle = useMemo(() => {
    const count = userMarkers.length;
    if (count < 2) return undefined;
    const MARKER_H = 3;          // marker height (px) — keep in sync with CSS
    const PADDING = 20;          // 10px top + 10px bottom
    const MIN_GAP = 4;
    const MAX_GAP = 12;
    const maxH = window.innerHeight * 0.55;
    const totalMarkerH = count * MARKER_H;
    const gapCount = count - 1;
    const needed = totalMarkerH + gapCount * MAX_GAP + PADDING;
    if (needed <= maxH) return { gap: MAX_GAP };
    const available = maxH - PADDING - totalMarkerH;
    return { gap: Math.max(MIN_GAP, Math.round(available / gapCount)) };
  }, [userMarkers.length]);

  // Hide minimap when fewer than 2 user messages
  if (userMarkers.length < 2) return null;

  return (
    <div
      ref={railRef}
      className="conversation-minimap"
      onMouseEnter={() => {
        if (closeTimerRef.current) {
          clearTimeout(closeTimerRef.current);
          closeTimerRef.current = null;
        }
        setExpanded(true);
      }}
      onMouseLeave={() => {
        closeTimerRef.current = setTimeout(() => {
          setExpanded(false);
        }, 200);
      }}
    >
      {/* Collapsed rail — thin markers */}
      <div className="minimap-marks" style={markerStyle}>
        {userMarkers.map((m, i) => (
          <div
            key={i}
            className={`minimap-mark ${m.idx === activeIdx ? 'active' : ''}`}
            onClick={() => handleMarkerClick(m.idx)}
            title={m.text}
          />
        ))}
      </div>

      {/* Expanded panel — hover tooltip */}
      {expanded && (
        <div className="minimap-panel">
          {userMarkers.map((m, i) => (
            <div
              key={i}
              className={`minimap-item ${m.idx === activeIdx ? 'active' : ''}`}
              onClick={() => handleMarkerClick(m.idx)}
            >
              <span className="minimap-item-idx">{i + 1}</span>
              <span className="minimap-item-text">{m.text || `Message ${i + 1}`}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
