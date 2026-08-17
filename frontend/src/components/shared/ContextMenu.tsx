import { useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';

export interface ContextMenuItem {
  label: string;
  icon?: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
}

export interface ContextMenuSeparator {
  separator: true;
}

export type ContextMenuEntry = ContextMenuItem | ContextMenuSeparator;

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuEntry[];
  onClose: () => void;
}

export default function ContextMenu({ x, y, items, onClose }: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  const handleClose = useCallback(
    (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent) {
        if (e.key === 'Escape') onClose();
        return;
      }
      // Click outside menu → close
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    },
    [onClose]
  );

  useEffect(() => {
    // Delay listener attachment so the contextmenu event doesn't immediately close it
    const timer = setTimeout(() => {
      document.addEventListener('click', handleClose);
      document.addEventListener('keydown', handleClose);
    }, 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('click', handleClose);
      document.removeEventListener('keydown', handleClose);
    };
  }, [handleClose]);

  // Prevent events on the menu from bubbling to the close handler
  const stopProp = (e: React.MouseEvent) => e.stopPropagation();

  // Adjust position to avoid screen edge overflow
  const adjustedX = Math.min(x, window.innerWidth - 160);
  const adjustedY = Math.min(y, window.innerHeight - items.length * 48 - 20);

  return createPortal(
    <div
      ref={menuRef}
      className="sidebar-item-action-menu"
      style={{
        position: 'fixed',
        left: adjustedX,
        top: adjustedY,
        zIndex: 9999,
      }}
      onClick={stopProp}
      onMouseDown={stopProp}
    >
      {items.map((item, i) => {
        if ('separator' in item) {
          return <div key={i} className="border-t border-[var(--border)] my-1" />;
        }
        const mi = item as ContextMenuItem;
        return (
          <button
            key={i}
            className={`sidebar-item-action-opt${mi.danger ? ' danger' : ''}`}
            onClick={() => {
              mi.onClick();
              onClose();
            }}
          >
            {mi.icon && <span className="mr-3 flex-shrink-0">{mi.icon}</span>}
            <span className="ws-opt-action">{mi.label}</span>
          </button>
        );
      })}
    </div>,
    document.body
  );
}
