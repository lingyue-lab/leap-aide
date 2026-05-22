import React, { useState, useRef, useCallback, useEffect } from 'react';
import Sidebar from '@/components/layout/Sidebar';
import MainContent from '@/components/layout/MainContent';
import RightPanel from '@/components/layout/RightPanel';

interface LayoutProps {
  children: React.ReactNode;
}

const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 500;
const DEFAULT_SIDEBAR_WIDTH = 300;

export default function Layout({ children }: LayoutProps) {
  const [rightPanelOpen, setRightPanelOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    // Try to restore from localStorage
    const saved = localStorage.getItem('hermes_sidebar_width');
    if (saved) {
      const parsed = parseInt(saved, 10);
      if (!isNaN(parsed) && parsed >= MIN_SIDEBAR_WIDTH && parsed <= MAX_SIDEBAR_WIDTH) {
        return parsed;
      }
    }
    return DEFAULT_SIDEBAR_WIDTH;
  });
  
  const isResizing = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);
  // Keep a ref to the latest sidebarWidth so handleMouseUp can read it without
  // being re-created on every width change (which would cause event-listener churn).
  const sidebarWidthRef = useRef(sidebarWidth);
  useEffect(() => { sidebarWidthRef.current = sidebarWidth; }, [sidebarWidth]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    startX.current = e.clientX;
    startWidth.current = sidebarWidthRef.current;
    setIsDragging(true);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isResizing.current) return;
    // Divide delta by --app-zoom because sidebar has zoom applied;
    // clientX is in screen pixels but sidebar width is in CSS (layout) pixels.
    const zoom = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--app-zoom').trim()) || 1;
    const delta = (e.clientX - startX.current) / zoom;
    const newWidth = Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, startWidth.current + delta));
    setSidebarWidth(newWidth);
  }, []);

  const handleMouseUp = useCallback(() => {
    if (isResizing.current) {
      isResizing.current = false;
      setIsDragging(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      // Save to localStorage using ref so this callback never goes stale
      localStorage.setItem('hermes_sidebar_width', sidebarWidthRef.current.toString());
    }
  }, []);

  useEffect(() => {
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [handleMouseMove, handleMouseUp]);

  return (
    <div className="layout">
      {/* Transparent full-screen overlay rendered while dragging the resize handle.
          This sits above the VNC canvas (and any other iframe/canvas content) so
          those elements cannot intercept mousemove / mouseup events during a drag. */}
      {isDragging && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 9999,
            cursor: 'col-resize',
          }}
        />
      )}
      <Sidebar width={sidebarWidth} onResizeStart={handleMouseDown} />
      <MainContent>
        {children}
      </MainContent>
      {rightPanelOpen && (
        <RightPanel isOpen={rightPanelOpen} onClose={() => setRightPanelOpen(false)} />
      )}
    </div>
  );
}
