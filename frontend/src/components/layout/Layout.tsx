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

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isResizing.current = true;
    startX.current = e.clientX;
    startWidth.current = sidebarWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [sidebarWidth]);

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isResizing.current) return;
    
    const delta = e.clientX - startX.current;
    const newWidth = Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, startWidth.current + delta));
    setSidebarWidth(newWidth);
  }, []);

  const handleMouseUp = useCallback(() => {
    if (isResizing.current) {
      isResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      // Save to localStorage
      localStorage.setItem('hermes_sidebar_width', sidebarWidth.toString());
    }
  }, [sidebarWidth]);

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
