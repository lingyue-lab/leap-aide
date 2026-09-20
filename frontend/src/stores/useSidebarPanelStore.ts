import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { NavPanel } from '@/types';

/**
 * Sidebar panel state, lifted from Sidebar.tsx local useState so it can be
 * driven programmatically from anywhere — e.g. the providers gate runner
 * needs to switch the sidebar list to "settings" when the user accepts
 * "Configure providers" in the gate dialog.
 *
 * `activePanel` is intentionally NOT persisted: the sidebar should always
 * open on Chat. `sidebarOpen` IS persisted so the user's preference
 * (collapsed vs expanded) survives page refresh; the storage key matches
 * the one previously used by Sidebar.tsx's local useState initializer
 * (`hermes_sidebar_open`) so existing users keep their setting.
 */
interface SidebarPanelState {
  activePanel: NavPanel;
  sidebarOpen: boolean;
  setActivePanel: (panel: NavPanel) => void;
  setSidebarOpen: (open: boolean) => void;
  toggleSidebarOpen: () => void;
}

export const useSidebarPanelStore = create<SidebarPanelState>()(
  persist(
    (set, get) => ({
      activePanel: 'chat',
      sidebarOpen: true,
      setActivePanel: (panel) => set({ activePanel: panel }),
      setSidebarOpen: (open) => set({ sidebarOpen: open }),
      toggleSidebarOpen: () => set({ sidebarOpen: !get().sidebarOpen }),
    }),
    {
      name: 'hermes_sidebar_open',
      // Only persist sidebarOpen — activePanel always resets to 'chat'
      // on reload, matching the previous local-state behavior.
      partialize: (state) => ({ sidebarOpen: state.sidebarOpen }),
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
