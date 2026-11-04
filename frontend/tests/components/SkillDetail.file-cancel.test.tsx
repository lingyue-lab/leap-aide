/**
 * Skill file editor: Cancel must discard the draft. Save is a checkpoint that
 * keeps edit mode open, so re-entering Edit for the same file must show the
 * on-disk content, not the cancelled draft.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import SkillDetail from '@/components/skills/SkillDetail';
import { useSkillStore } from '@/stores/useSkillStore';
import { skillsService } from '@/services/api';

const NOTE = '/ws/demo-skill/notes.md';

// Expose the tree callbacks as buttons: the real panel is a context menu, and
// this test only cares about the edit/cancel lifecycle it drives.
vi.mock('@/components/shared/FileTreePanel', () => ({
  default: (props: {
    onFileSelect?: (path: string) => void;
    onEdit?: (path: string) => void;
  }) => (
    <div>
      <button onClick={() => props.onFileSelect?.(NOTE)}>select-note</button>
      <button onClick={() => props.onEdit?.(NOTE)}>edit-note</button>
    </div>
  ),
}));

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

const mockedGetContent = vi.mocked(skillsService.getContent);

function seed() {
  useSkillStore.setState({
    selectedSkill: {
      name: 'demo-skill',
      label: 'Demo Skill',
      category: 'general',
    },
    selectedSkillContent: '# Demo',
    loadingContent: false,
    isEditing: false,
    showFiles: false,
  });
}

function renderSkill() {
  return render(<SkillDetail />);
}

async function openEditor() {
  // FileTreePanel is lazy-loaded, so its mocked callbacks appear after the
  // Suspense fallback resolves.
  fireEvent.click(await screen.findByText('select-note'));
  // Wait until the on-disk content has loaded into the preview.
  expect(await screen.findByText('on disk')).toBeInTheDocument();
  fireEvent.click(screen.getByText('edit-note'));
  return waitFor(() => {
    const textarea = document.querySelector('textarea');
    expect(textarea).not.toBeNull();
    return textarea as HTMLTextAreaElement;
  });
}

describe('Skill panel — Cancel discards the file draft', () => {
  beforeEach(() => {
    seed();
    mockedGetContent.mockReset();
    mockedGetContent.mockResolvedValue({ content: 'on disk' });
  });

  it('restores the on-disk content after Cancel and on the next Edit', async () => {
    renderSkill();
    const textarea = await openEditor();
    expect(textarea.value).toBe('on disk');

    fireEvent.change(textarea, { target: { value: 'cancelled draft' } });
    expect(textarea.value).toBe('cancelled draft');

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(document.querySelector('textarea')).toBeNull();

    // Re-entering Edit for the same file (activeFile does not change, so the
    // load effect does not re-run) must not resurrect the cancelled draft.
    fireEvent.click(screen.getByText('edit-note'));
    const reopened = await waitFor(() => {
      const next = document.querySelector('textarea');
      expect(next).not.toBeNull();
      return next as HTMLTextAreaElement;
    });
    expect(reopened.value).toBe('on disk');
  });
});
