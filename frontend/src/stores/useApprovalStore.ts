import { create } from 'zustand';
import { approvalService } from '@/services/api';

interface ApprovalState {
  sessionApproved: string[];
  permanentApproved: string[];
  loading: boolean;
  error: string | null;

  // Actions
  loadApproved: (sessionId: string) => Promise<void>;
  addApproved: (patterns: string[]) => void;
  revokeApproved: (sessionId: string, patternKey: string) => Promise<void>;
  reset: () => void;
}

export const useApprovalStore = create<ApprovalState>((set, get) => ({
  sessionApproved: [],
  permanentApproved: [],
  loading: false,
  error: null,

  loadApproved: async (sessionId: string) => {
    if (!sessionId) return;
    set({ loading: true, error: null });
    try {
      const data = await approvalService.getSessionApproved(sessionId);
      set({
        sessionApproved: data.session_approved,
        permanentApproved: data.permanent_approved,
        loading: false,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load approvals';
      set({ error: message, loading: false });
    }
  },

  addApproved: (patterns: string[]) => {
    const current = get().sessionApproved;
    const added = patterns.filter((p) => !current.includes(p));
    if (added.length > 0) {
      set({ sessionApproved: [...current, ...added] });
    }
  },

  revokeApproved: async (sessionId: string, patternKey: string) => {
    const previous = get().sessionApproved;
    // Optimistic update
    set({
      sessionApproved: previous.filter((p) => p !== patternKey),
      error: null,
    });
    try {
      const data = await approvalService.revoke(sessionId, patternKey);
      if (!data.ok) {
        // Backend failed to revoke — revert
        set({ sessionApproved: previous });
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to revoke approval';
      set({ sessionApproved: previous, error: message });
    }
  },

  reset: () => {
    set({
      sessionApproved: [],
      permanentApproved: [],
      loading: false,
      error: null,
    });
  },
}));
