import { create } from 'zustand';

export type SettingsGroup = 'appearance' | 'model' | 'notifications' | 'about' | 'config' | 'providers';

interface SettingsTabState {
  settingsTabActive: boolean;
  selectedGroup: SettingsGroup;
  setSettingsTabActive: (active: boolean) => void;
  setSelectedGroup: (group: SettingsGroup) => void;
}

export const useSettingsTabStore = create<SettingsTabState>((set) => ({
  settingsTabActive: false,
  selectedGroup: 'appearance',
  setSettingsTabActive: (active) => set({ settingsTabActive: active }),
  setSelectedGroup: (group) => set({ selectedGroup: group }),
}));
