import { create } from 'zustand';
import i18n from '@/i18n/config';
import type { Settings, Theme } from '@/types';

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
  show_token_usage: true,
  show_cli_sessions: false,
  sync_insights: false,
  check_updates: true,
  bot_name: 'Hermes',
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
      const merged = { ...defaultSettings, ...settings };
      set({ settings: merged, loading: false });

      // Apply font size immediately after load
      const fontSize = merged.font_size || 'medium';
      document.documentElement.setAttribute('data-font-size', fontSize);
      const zoomMap: Record<string, string> = { small: '0.8', medium: '1', large: '1.3' };
      document.documentElement.style.setProperty('--app-zoom', zoomMap[fontSize] || '1');
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
        const zoomMap: Record<string, string> = { small: '0.8', medium: '1', large: '1.3' };
        document.documentElement.style.setProperty('--app-zoom', zoomMap[updates.font_size] || '1');
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
