import { create } from 'zustand';
import type { Skill } from '@/types';

interface SkillState {
  skills: Skill[];
  loadingSkills: boolean;
  selectedSkill: Skill | null;
  selectedSkillContent: string | null;
  loadingContent: boolean;
  skillsTabActive: boolean;
  isEditing: boolean;

  loadSkills: () => Promise<void>;
  selectSkill: (skill: Skill) => Promise<void>;
  clearSelection: () => void;
  setSelectedSkillContent: (content: string | null) => void;
  setSkillsTabActive: (active: boolean) => void;
  setIsEditing: (editing: boolean) => void;
  cancelEditing: () => void;
}

export const useSkillStore = create<SkillState>((set) => ({
  skills: [],
  loadingSkills: false,
  selectedSkill: null,
  selectedSkillContent: null,
  loadingContent: false,
  skillsTabActive: false,
  isEditing: false,

  loadSkills: async () => {
    set({ loadingSkills: true });
    try {
      const res = await fetch('/api/skills', { credentials: 'include', cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        set({ skills: data.skills || [], loadingSkills: false });
      } else {
        set({ loadingSkills: false });
      }
    } catch {
      set({ loadingSkills: false });
    }
  },

  selectSkill: async (skill) => {
    set({ selectedSkill: skill, selectedSkillContent: null, loadingContent: true });
    try {
      const lookupName = skill.dir_name || skill.name;
      const response = await fetch(
        `/api/skills/content?${new URLSearchParams({ name: lookupName })}`,
        { credentials: 'include', cache: 'no-store' }
      );
      if (response.ok) {
        const data = await response.json();
        set({ selectedSkillContent: data.content, loadingContent: false });
      } else {
        set({ selectedSkillContent: skill.content || '', loadingContent: false });
      }
    } catch {
      set({ selectedSkillContent: skill.content || '', loadingContent: false });
    }
  },

  clearSelection: () => {
    set({ selectedSkill: null, selectedSkillContent: null, loadingContent: false });
  },

  setSelectedSkillContent: (content) => {
    set({ selectedSkillContent: content });
  },

  setSkillsTabActive: (active) => {
    set({ skillsTabActive: active });
  },

  setIsEditing: (editing) => {
    set({ isEditing: editing });
  },

  cancelEditing: () => {
    set({ isEditing: false });
  },
}));
