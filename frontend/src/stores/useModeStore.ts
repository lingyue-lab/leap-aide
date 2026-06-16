import { create } from 'zustand';
import { fetchMode, setMode } from '@/services/modeService';

export type ApprovalMode = 'manual' | 'smart' | 'yolo';

interface ModeState {
  currentMode: ApprovalMode;
  smartSupported: boolean;
  loading: boolean;
  error: string | null;

  // Actions
  loadMode: (sessionId: string) => Promise<void>;
  setSessionMode: (sessionId: string, mode: ApprovalMode) => Promise<void>;
  syncFromEvent: (mode: ApprovalMode) => void;
  reset: () => void;
}

export const useModeStore = create<ModeState>((set, get) => ({
  currentMode: 'manual',
  smartSupported: false,
  loading: false,
  error: null,

  loadMode: async (sessionId: string) => {
    if (!sessionId) return;
    set({ loading: true, error: null });
    try {
      const data = await fetchMode(sessionId);
      set({
        currentMode: data.mode,
        smartSupported: data.smart_supported,
        loading: false,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load mode';
      set({ error: message, loading: false });
    }
  },

  setSessionMode: async (sessionId: string, mode: ApprovalMode) => {
    const previousMode = get().currentMode;
    // Optimistic update
    set({ currentMode: mode, error: null });
    try {
      const data = await setMode(sessionId, mode);
      set({
        currentMode: data.mode,
        smartSupported: data.smart_supported,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to set mode';
      // Revert on failure
      set({ currentMode: previousMode, error: message });
    }
  },

  syncFromEvent: (mode: ApprovalMode) => {
    set({ currentMode: mode });
  },

  reset: () => {
    set({
      currentMode: 'manual',
      smartSupported: false,
      loading: false,
      error: null,
    });
  },
}));
