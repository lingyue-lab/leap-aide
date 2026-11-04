import { describe, it, expect } from 'vitest';
import {
  addNodeChildSorted,
  removeNodeById,
  renameNodeById,
  type ArboristNode,
} from '@/components/shared/FileTreePanel';

function node(path: string, isDir = false, children?: ArboristNode[]): ArboristNode {
  return { id: path, name: path.split('/').pop() || path, path, isDir, children };
}

describe('FileTreePanel — immediate tree updates after file operations', () => {
  const tree: ArboristNode[] = [
    node('/ws/docs', true, [
      node('/ws/docs/a.md'),
      node('/ws/docs/nested', true, [node('/ws/docs/nested/b.md')]),
    ]),
    node('/ws/readme.md'),
  ];

  it('renames a file in place', () => {
    const next = renameNodeById(tree, '/ws/readme.md', 'README.md', '/ws/README.md');

    expect(next[1]).toMatchObject({
      id: '/ws/README.md',
      path: '/ws/README.md',
      name: 'README.md',
    });
    expect(next[0]).toBe(tree[0]);
  });

  it('renames a directory and relocates every descendant path', () => {
    const next = renameNodeById(tree, '/ws/docs', 'documentation', '/ws/documentation');

    expect(next[0]).toMatchObject({
      id: '/ws/documentation',
      path: '/ws/documentation',
      name: 'documentation',
    });
    expect(next[0].children?.map((c) => c.id)).toEqual([
      '/ws/documentation/a.md',
      '/ws/documentation/nested',
    ]);
    expect(next[0].children?.[1].children?.[0]).toMatchObject({
      id: '/ws/documentation/nested/b.md',
      path: '/ws/documentation/nested/b.md',
      name: 'b.md',
    });
  });

  it('removes a nested node without touching siblings', () => {
    const next = removeNodeById(tree, '/ws/docs/nested/b.md');

    expect(next[0].children?.[1].children).toEqual([]);
    expect(next[1].id).toBe('/ws/readme.md');
  });

  it('inserts created children sorted and skips duplicates', () => {
    const withFile = addNodeChildSorted(tree, '/ws/docs', node('/ws/docs/z.txt'));
    expect(withFile[0].children?.map((c) => c.name)).toEqual(['nested', 'a.md', 'z.txt']);

    const duplicate = addNodeChildSorted(withFile, '/ws/docs', node('/ws/docs/z.txt'));
    expect(duplicate[0].children?.filter((c) => c.id === '/ws/docs/z.txt')).toHaveLength(1);
  });
});
