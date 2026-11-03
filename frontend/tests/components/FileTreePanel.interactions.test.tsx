import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import FileTreePanel from '@/components/shared/FileTreePanel';
import type { FileTreeNode } from '@/components/shared/FileTreePanel';

const tree: FileTreeNode[] = [
  {
    name: 'docs',
    path: '/ws/docs',
    is_dir: true,
    children: [{ name: 'a.md', path: '/ws/docs/a.md', is_dir: false }],
  },
  { name: 'readme.md', path: '/ws/readme.md', is_dir: false },
];

function renderPanel(overrides: Partial<React.ComponentProps<typeof FileTreePanel>> = {}) {
  const props = {
    visible: true,
    tree,
    defaultExpanded: true,
    onCreate: vi.fn().mockResolvedValue(undefined),
    onRename: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  render(<FileTreePanel {...props} />);
  return props;
}

describe('FileTreePanel — context-menu operations update in place', () => {
  beforeEach(() => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renames a file immediately without a reload', async () => {
    const props = renderPanel();

    fireEvent.contextMenu(await screen.findByText('readme.md'));
    fireEvent.click(screen.getByText('Rename'));

    const input = await screen.findByDisplayValue('readme.md');
    fireEvent.change(input, { target: { value: 'README.md' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(props.onRename).toHaveBeenCalledWith('/ws/readme.md', '/ws/README.md', '/ws')
    );
    expect(await screen.findByText('README.md')).toBeInTheDocument();
    expect(screen.queryByText('readme.md')).not.toBeInTheDocument();
  });

  it('deletes a file immediately without a reload', async () => {
    const props = renderPanel();

    fireEvent.contextMenu(await screen.findByText('a.md'));
    fireEvent.click(screen.getByText('Delete'));

    await waitFor(() =>
      expect(props.onDelete).toHaveBeenCalledWith('/ws/docs/a.md', false)
    );
    await waitFor(() => expect(screen.queryByText('a.md')).not.toBeInTheDocument());
    // The parent directory stays expanded: its other contents are untouched.
    expect(screen.getByText('docs')).toBeInTheDocument();
  });

  it('creates a file and keeps every expanded directory open', async () => {
    const props = renderPanel();

    // Expanded child is visible before the create.
    expect(await screen.findByText('a.md')).toBeInTheDocument();

    fireEvent.contextMenu(screen.getByText('docs'));
    fireEvent.click(screen.getByText('New File'));

    const input = await screen.findByPlaceholderText('file name');
    fireEvent.change(input, { target: { value: 'b.md' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() =>
      expect(props.onCreate).toHaveBeenCalledWith('/ws/docs', 'b.md', false)
    );
    // The created row appears without a reload…
    expect(await screen.findByText('b.md')).toBeInTheDocument();
    // …and the directory stays expanded (its existing child is still shown).
    expect(screen.getByText('a.md')).toBeInTheDocument();
  });

  it('keeps expanded directories open when creating inside them (lazy loader)', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    const loader = vi.fn(async (path: string): Promise<FileTreeNode[]> => {
      if (path === '/ws') {
        return [
          { name: 'docs', path: '/ws/docs', is_dir: true },
          { name: 'readme.md', path: '/ws/readme.md', is_dir: false },
        ];
      }
      if (path === '/ws/docs') {
        return [{ name: 'a.md', path: '/ws/docs/a.md', is_dir: false }];
      }
      return [];
    });

    render(
      <FileTreePanel
        visible
        rootPath="/ws"
        loader={loader}
        loadKey="/ws"
        onCreate={onCreate}
      />
    );

    fireEvent.click(await screen.findByText('docs'));
    expect(await screen.findByText('a.md')).toBeInTheDocument();

    fireEvent.contextMenu(screen.getByText('docs'));
    fireEvent.click(screen.getByText('New File'));

    const input = await screen.findByPlaceholderText('file name');
    fireEvent.change(input, { target: { value: 'b.md' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith('/ws/docs', 'b.md', false));
    expect(await screen.findByText('b.md')).toBeInTheDocument();
    // Regression guard: the root loader must not be re-run after creation.
    expect(loader).toHaveBeenCalledTimes(2); // root once, docs once
    expect(screen.getByText('a.md')).toBeInTheDocument();
  });
});
