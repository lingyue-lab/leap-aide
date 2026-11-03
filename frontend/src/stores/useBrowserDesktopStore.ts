import { create } from 'zustand';
import { api } from '@/services/api';

/** Response shape of GET /api/browser-desktop (backend/app/api/browser_desktop.py). */
export interface BrowserDesktopInfo {
  ok: boolean;
  running: boolean;
  enabled: boolean;
  chrome_alive: boolean;
  vnc_port?: number;
  display_num?: number;
  receptionist_port?: number;
  receptionist_url?: string;
  status?: string;
  error?: string;
}

export type BrowserDesktopStatus = 'active' | 'degraded' | 'stopped' | 'disabled';

/** Collapse the status payload into the single state the UI renders. */
export function getBrowserDesktopStatus(info: BrowserDesktopInfo | null): BrowserDesktopStatus {
  if (!info || !info.enabled) return 'disabled';
  if (info.ok) return 'active';
  if (info.running) return 'degraded';
  return 'stopped';
}

interface BrowserDesktopState {
  /** True while the browser-desktop nav panel is the active sidebar panel.
   *  Mirrors terminalTabActive in useTerminalStore: drives the keep-mounted
   *  main-area visibility toggle in MainContent. */
  browserDesktopActive: boolean;
  /** Latest GET /api/browser-desktop payload, or null before first load. */
  info: BrowserDesktopInfo | null;
  loading: boolean;
  /** Bumped by the reconnect button; BrowserDesktopView watches this token
   *  and tears down / re-creates its noVNC RFB connection when it changes. */
  reconnectToken: number;

  setBrowserDesktopActive: (active: boolean) => void;
  load: () => Promise<void>;
  /** Force a status refresh + RFB reconnect (panel header button). */
  reconnect: () => void;
}

export const useBrowserDesktopStore = create<BrowserDesktopState>((set, get) => ({
  browserDesktopActive: false,
  info: null,
  loading: false,
  reconnectToken: 0,

  setBrowserDesktopActive: (active) => {
    set({ browserDesktopActive: active });
    if (active) void get().load();
  },

  load: async () => {
    set({ loading: true });
    try {
      const info = await api<BrowserDesktopInfo>('/api/browser-desktop');
      set({ info, loading: false });
    } catch {
      // Backend unreachable — surface as a stopped desktop rather than
      // leaving a stale status chip on screen.
      set({
        info: {
          ok: false,
          running: false,
          enabled: true,
          chrome_alive: false,
          status: 'error',
          error: 'browser-desktop status unavailable',
        },
        loading: false,
      });
    }
  },

  reconnect: () => {
    set((s) => ({ reconnectToken: s.reconnectToken + 1 }));
    void get().load();
  },
}));
