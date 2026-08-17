import { useState, useRef, useCallback, useLayoutEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

interface TooltipProps {
  text: string;
  children: ReactNode;
}

const PADDING = 12; // min px from viewport edge

/** Zero-delay tooltip rendered via portal — follows the mouse cursor. */
export default function Tooltip({ text, children }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const tipRef = useRef<HTMLDivElement>(null);
  const latestMouse = useRef({ x: 0, y: 0 });

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    latestMouse.current = { x: e.clientX, y: e.clientY };
    if (!visible) return;
    setPos({ top: e.clientY + 16, left: e.clientX });
  }, [visible]);

  const show = useCallback((e: React.MouseEvent) => {
    latestMouse.current = { x: e.clientX, y: e.clientY };
    setPos({ top: e.clientY + 16, left: e.clientX });
    setVisible(true);
  }, []);

  // Clamp to viewport after paint
  useLayoutEffect(() => {
    if (!visible || !tipRef.current) return;
    const tip = tipRef.current.getBoundingClientRect();
    let adjustX = 0;
    let adjustY = 0;
    if (tip.right > window.innerWidth - PADDING) adjustX = tip.right - window.innerWidth + PADDING;
    if (tip.left < PADDING) adjustX = -(PADDING - tip.left);
    if (tip.bottom > window.innerHeight - PADDING) adjustY = tip.bottom - window.innerHeight + PADDING;
    if (adjustX || adjustY) {
      setPos(p => ({ top: p.top - adjustY, left: p.left - adjustX }));
    }
  }, [visible, pos]);

  const hide = useCallback(() => setVisible(false), []);

  return (
    <span
      onMouseEnter={show}
      onMouseMove={onMouseMove}
      onMouseLeave={hide}
      style={{ display: 'inline-flex' }}
    >
      {children}
      {visible &&
        createPortal(
          <div
            ref={tipRef}
            style={{
              position: 'fixed',
              top: pos.top,
              left: pos.left,
              transform: 'translate(-50%, 0)',
              padding: '4px 8px',
              background: 'rgba(0, 0, 0, 0.78)',
              color: 'rgba(255, 255, 255, 0.9)',
              fontSize: 13,
              fontWeight: 400,
              lineHeight: 1.3,
              borderRadius: 5,
              border: '1px solid rgba(255,255,255,0.12)',
              whiteSpace: 'nowrap',
              zIndex: 99999,
              pointerEvents: 'none',
              boxShadow: '0 2px 12px rgba(0,0,0,0.5)',
              animation: 'tooltip-in 0.08s ease-out',
            }}
          >
            {text}
          </div>,
          document.body
        )}
    </span>
  );
}
