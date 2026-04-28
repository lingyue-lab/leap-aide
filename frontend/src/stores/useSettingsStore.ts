import { create } from 'zustand';
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
  sound_enabled: true,
  notifications_enabled: false,
  show_token_usage: true,
  bubble_layout: false,
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
      set({ settings: { ...defaultSettings, ...settings }, loading: false });
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
  },
}));
