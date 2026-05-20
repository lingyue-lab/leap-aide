import { create } from 'zustand';

export interface VncConfig {
  /** VNC server password (passed to noVNC credentials) */
  password: string;
}

const STORAGE_KEY = 'hermes_vnc_saved_password';

/** Persist password to localStorage. */
function savePassword(pw: string) {
  try {
    localStorage.setItem(STORAGE_KEY, pw);
  } catch (_) { /* storage unavailable */ }
}

/** Remove saved password from localStorage. */
function clearSavedPassword() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (_) { /* ignore */ }
}

/** Read saved password from localStorage (empty string if not set). */
function loadSavedPassword(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? '';
  } catch (_) {
    return '';
  }
}

interface VncState {
  vncTabActive: boolean;
  connected: boolean;
  config: VncConfig;

  /** Whether the password should be saved to localStorage. */
  rememberPassword: boolean;

  /**
   * Callback registered by VncView so that other components (e.g. VncPanel sidebar)
   * can trigger a disconnect without needing a direct ref to the RFB instance.
   */
  _disconnectFn: (() => void) | null;

  setVncTabActive: (active: boolean) => void;
  setConnected: (connected: boolean) => void;
  setConfig: (config: Partial<VncConfig>) => void;
  setRememberPassword: (remember: boolean) => void;
  /** Called by VncView on mount to register its disconnect handler. */
  registerDisconnect: (fn: (() => void) | null) => void;
  /** Called by any consumer (e.g. sidebar) to disconnect the active session. */
  disconnect: () => void;
}

const savedPw = loadSavedPassword();

export const useVncStore = create<VncState>((set, get) => ({
  vncTabActive: false,
  connected: false,
  config: {
    // Pre-populate with saved password if one exists.
    password: savedPw,
  },
  rememberPassword: savedPw !== '',
  _disconnectFn: null,

  setVncTabActive: (active) => set({ vncTabActive: active }),
  setConnected: (connected) => set({ connected }),
  setConfig: (config) =>
    set((s) => ({ config: { ...s.config, ...config } })),

  setRememberPassword: (remember) => {
    set({ rememberPassword: remember });
    if (!remember) {
      // Clear saved password immediately when user unchecks.
      clearSavedPassword();
    }
  },

  registerDisconnect: (fn) => set({ _disconnectFn: fn }),
  disconnect: () => {
    const fn = get()._disconnectFn;
    if (fn) fn();
  },
}));

/** Call this after a successful connect to persist the password if rememberPassword is on. */
export function maybeSavePassword(password: string, remember: boolean) {
  if (remember && password) {
    savePassword(password);
  } else {
    clearSavedPassword();
  }
}
