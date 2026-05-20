import { create } from 'zustand';

export interface TerminalInstance {
  id: string;
  name: string;
  createdAt: number;
}

let _nextId = 1;
const newId = () => `term-${_nextId++}`;

interface TerminalState {
  terminalTabActive: boolean;
  terminals: TerminalInstance[];
  activeTerminalId: string | null;

  setTerminalTabActive: (active: boolean) => void;
  addTerminal: () => void;
  removeTerminal: (id: string) => void;
  selectTerminal: (id: string) => void;
  renameTerminal: (id: string, name: string) => void;
}

const makeTerminal = (index: number): TerminalInstance => ({
  id: newId(),
  name: `Terminal ${index}`,
  createdAt: Date.now(),
});

export const useTerminalStore = create<TerminalState>((set, get) => {
  const first = makeTerminal(1);
  return {
    terminalTabActive: false,
    terminals: [first],
    activeTerminalId: first.id,

    setTerminalTabActive: (active) => set({ terminalTabActive: active }),

    addTerminal: () => {
      const index = get().terminals.length + 1;
      const t = makeTerminal(index);
      set((s) => ({
        terminals: [...s.terminals, t],
        activeTerminalId: t.id,
      }));
    },

    removeTerminal: (id) => {
      set((s) => {
        const filtered = s.terminals.filter((t) => t.id !== id);
        if (filtered.length === 0) {
          const fresh = makeTerminal(1);
          return { terminals: [fresh], activeTerminalId: fresh.id };
        }
        const newActive =
          s.activeTerminalId === id
            ? filtered[filtered.length - 1].id
            : s.activeTerminalId;
        return { terminals: filtered, activeTerminalId: newActive };
      });
    },

    selectTerminal: (id) => set({ activeTerminalId: id }),

    renameTerminal: (id, name) =>
      set((s) => ({
        terminals: s.terminals.map((t) => (t.id === id ? { ...t, name } : t)),
      })),
  };
});
