import { useEffect } from 'react';
import { useModeStore } from '@/stores/useModeStore';
import { useSessionStore } from '@/stores/useSessionStore';

const MODE_ORDER: Array<'manual' | 'smart' | 'yolo'> = ['manual', 'smart', 'yolo'];

/**
 * Global keyboard shortcut: Shift+Tab cycles through approval modes
 * (manual → smart → yolo → manual).  Unsupported modes are skipped.
 */
export function useModeShortcut() {
  const { currentMode, smartSupported, setSessionMode } = useModeStore();
  const { currentSession } = useSessionStore();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Tab' && e.shiftKey) {
        e.preventDefault();
        const sessionId = currentSession?.session_id;
        if (!sessionId) return;

        const idx = MODE_ORDER.indexOf(currentMode);
        let next = (idx + 1) % MODE_ORDER.length;
        // Skip unsupported modes
        while (MODE_ORDER[next] === 'smart' && !smartSupported) {
          next = (next + 1) % MODE_ORDER.length;
        }
        // No-op if we'd land on the same mode
        if (MODE_ORDER[next] === currentMode) return;
        setSessionMode(sessionId, MODE_ORDER[next]);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [currentMode, smartSupported, currentSession, setSessionMode]);
}
