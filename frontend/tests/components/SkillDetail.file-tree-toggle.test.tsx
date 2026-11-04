import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import SkillDetail from '@/components/skills/SkillDetail';
import { useSkillStore } from '@/stores/useSkillStore';

// The sidebar's own header Close button is the single close affordance; this
// test asserts the topbar's duplicate folder toggle disappears while it is open.
vi.mock('@/components/shared/FileTreePanel', () => ({ default: () => null }));

vi.mock('@/services/api', () => ({
  skillsService: {
    getFiles: vi.fn().mockResolvedValue({ tree: [] }),
    getContent: vi.fn(),
    save: vi.fn(),
    saveFile: vi.fn(),
    createFile: vi.fn(),
    renameFile: vi.fn(),
    deleteFile: vi.fn(),
  },
}));

function seed(showFiles: boolean) {
  useSkillStore.setState({
    selectedSkill: {
      name: 'demo-skill',
      label: 'Demo Skill',
      category: 'general',
    },
    selectedSkillContent: '# Demo',
    loadingContent: false,
    isEditing: false,
    showFiles,
  });
}

describe('Skill panel — single close affordance for the file tree', () => {
  beforeEach(() => seed(false));

  it('shows the folder button while the sidebar is closed', () => {
    render(<SkillDetail />);
    expect(screen.getByTitle('Show files')).toBeInTheDocument();
  });

  it('hides the folder button while the sidebar is open', () => {
    seed(true);
    render(<SkillDetail />);
    expect(screen.queryByTitle('Show files')).not.toBeInTheDocument();
  });
});
