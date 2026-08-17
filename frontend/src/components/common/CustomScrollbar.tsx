import React, { useRef, useState, useCallback, useEffect, forwardRef } from 'react';

interface CustomScrollbarProps {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  onWheel?: (e: React.WheelEvent) => void;
  onTouchMove?: (e: React.TouchEvent) => void;
  onScroll?: (e: React.UIEvent<HTMLDivElement>) => void;
}

/**
 * Scrollable container with a custom overlay scrollbar track.
 * Uses a flex layout so the track is always visible alongside content.
 * Exposes the scroll container ref via forwardRef.
 */
const CustomScrollbar = forwardRef<HTMLDivElement, CustomScrollbarProps>(
  ({ children, className, style, onWheel, onTouchMove, onScroll: onScrollProp }, ref) => {
  const contentRef = useRef<HTMLDivElement>(null);
  const [thumbH, setThumbH] = useState(0);
  const [thumbY, setThumbY] = useState(0);
  const [showThumb, setShowThumb] = useState(false);

  const updateThumb = useCallback(() => {
    const ct = contentRef.current;
    if (!ct) return;
    const ch = ct.scrollHeight;
    const vh = ct.clientHeight;
    if (ch <= vh) {
      setShowThumb(false);
      return;
    }
    setShowThumb(true);
    const ratio = vh / ch;
    const h = Math.max(20, vh * ratio);
    setThumbH(h);
    setThumbY((ct.scrollTop / (ch - vh)) * (vh - h));
  }, []);

  const onScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    updateThumb();
    onScrollProp?.(e);
  }, [updateThumb, onScrollProp]);

  useEffect(() => {
    updateThumb();
    const ct = contentRef.current;
    if (!ct) return;
    const ro = new ResizeObserver(updateThumb);
    ro.observe(ct);
    return () => ro.disconnect();
  }, [updateThumb, children]);

  const outerStyle: React.CSSProperties = {
    ...style,
    position: 'relative',
    overflow: 'hidden',
  };

  return (
    <div className={className} style={outerStyle}>
      {/* ── scrollable content ── */}
      <div
        ref={(node) => {
          (contentRef as React.MutableRefObject<HTMLDivElement | null>).current = node;
          if (typeof ref === 'function') ref(node);
          else if (ref) (ref as React.MutableRefObject<HTMLDivElement | null>).current = node;
        }}
        className="custom-scroll-content"
        onScroll={onScroll}
        onWheel={onWheel}
        onTouchMove={onTouchMove}
        style={{
          height: '100%',
          overflowY: 'auto',
        }}
      >
        {children}
      </div>

      {/* ── overlay scrollbar track ── */}
      <div className="custom-scroll-track">
        {showThumb && (
          <div
            className="custom-scroll-thumb"
            style={{
              top: thumbY,
              height: thumbH,
            }}
          />
        )}
      </div>
    </div>
  );
});
CustomScrollbar.displayName = 'CustomScrollbar';
export default CustomScrollbar;
