import { create } from 'zustand';
import { memoryService } from '@/services/api';
import type { MemoryData } from '@/types';

export type MemoryFile = 'memory' | 'project';

interface MemoryState {
  memoryTabActive: boolean;
  selectedFile: MemoryFile | null;
  memoryData: MemoryData | null;
  loading: boolean;
  error: string | null;

  setMemoryTabActive: (active: boolean) => void;
  selectFile: (file: MemoryFile) => void;
  loadMemory: () => Promise<void>;
  writeMemory: (section: MemoryFile, content: string) => Promise<void>;
}

export const useMemoryStore = create<MemoryState>((set, get) => ({
  memoryTabActive: false,
  selectedFile: null,
  memoryData: null,
  loading: false,
  error: null,

  setMemoryTabActive: (active) => {
    set({ memoryTabActive: active });
    if (active && !get().memoryData) {
      get().loadMemory();
    }
  },

  selectFile: (file) => {
    set({ selectedFile: file });
  },

  loadMemory: async () => {
    set({ loading: true, error: null });
    try {
      const data = await memoryService.get();
      set({ memoryData: data, loading: false });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Failed to load memory', loading: false });
    }
  },

  writeMemory: async (section, content) => {
    await memoryService.write(section, content);
    await get().loadMemory();
  },
}));
