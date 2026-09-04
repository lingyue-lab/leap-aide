import { create } from 'zustand';
import i18n from '@/i18n/config';
import type { Settings, Theme } from '@/types';
import { FONT_ZOOM_MAP } from '@/constants';

interface SettingsState {
  settings: Settings | null;
  loading: boolean;
  error: string | null;
  
  // Actions
  loadSettings: () => Promise<void>;
  updateSettings: (updates: Partial<Settings>) => Promise<void>;
  setTheme: (theme: Theme) => void;
  setLanguage: (language: string) => void;
}

const defaultSettings: Settings = {
  model: 'anthropic/claude-sonnet-4-5',
  send_key: 'enter',
  theme: 'dark',
  language: 'en',
  font_size: 'medium',
  sound_enabled: true,
  notifications_enabled: false,
  show_cli_sessions: true,
  show_messaging_sessions: true,
  show_cron_sessions: false,
  check_for_updates: true,
  busy_input_mode: 'interrupt',
  session_poll_interval_sec: '10',
  approval_timeout_sec: '60',
  clarify_timeout_sec: '600',
  observer_tab_cleanup: 'session',
};

export const useSettingsStore = create<SettingsState>((set, get) => ({
  settings: null,
  loading: false,
  error: null,

  loadSettings: async () => {
    set({ loading: true, error: null });
    try {
      const response = await fetch('/api/settings', { credentials: 'include' });
      if (!response.ok) throw new Error('Failed to load settings');
      const settings = await response.json();
      // Backend returns 'default_model'; map it to the frontend 'model' key.
      if (settings.default_model && !settings.model) {
        settings.model = settings.default_model;
      }
      const merged = { ...defaultSettings, ...settings };
      // Clamp legacy theme values (slate/solarized/monokai/nord/oled) to a valid one.
      if (merged.theme !== 'system' && merged.theme !== 'dark' && merged.theme !== 'light') {
        merged.theme = 'dark';
      }
      set({ settings: merged, loading: false });

      // Apply font size immediately after load
      const fontSize = merged.font_size || 'medium';
      document.documentElement.setAttribute('data-font-size', fontSize);
      document.documentElement.style.setProperty('--app-zoom', FONT_ZOOM_MAP[fontSize] || FONT_ZOOM_MAP.medium);
      localStorage.setItem('hermes-font-size', fontSize);

      // Apply language immediately after load
      if (merged.language) {
        i18n.changeLanguage(merged.language);
      }
    } catch (err) {
      set({ 
        settings: defaultSettings, 
        error: (err as Error).message, 
        loading: false 
      });
    }
  },

  updateSettings: async (updates) => {
    const currentSettings = get().settings || defaultSettings;
    const newSettings = { ...currentSettings, ...updates };
    
    try {
      const response = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSettings),
        credentials: 'include',
      });
      if (!response.ok) throw new Error('Failed to save settings');
      set({ settings: newSettings });
      
      // Apply language change if language was updated
      if (updates.language && updates.language !== currentSettings.language) {
        i18n.changeLanguage(updates.language);
      }
      
      // Apply font size change immediately
      if (updates.font_size) {
        document.documentElement.setAttribute('data-font-size', updates.font_size);
        document.documentElement.style.setProperty('--app-zoom', FONT_ZOOM_MAP[updates.font_size] || FONT_ZOOM_MAP.medium);
        localStorage.setItem('hermes-font-size', updates.font_size);
      }
    } catch (err) {
      set({ error: (err as Error).message });
      throw err;
    }
  },

  setTheme: (theme) => {
    const currentSettings = get().settings || defaultSettings;
    set({ settings: { ...currentSettings, theme } });
    localStorage.setItem('hermes-theme', theme);
  },

  setLanguage: (language) => {
    const currentSettings = get().settings || defaultSettings;
    set({ settings: { ...currentSettings, language } });
    i18n.changeLanguage(language);
  },
}));
