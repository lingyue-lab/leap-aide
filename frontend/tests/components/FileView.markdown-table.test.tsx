import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import FileView from '@/components/shared/FileView';

/**
 * DOM contract test for issue #41: a GFM table in the workspace file preview
 * had centered headers and columns squeezed by a long inline-code path.
 *
 * This is not a visual regression test. The layout fix lives in CSS
 * (`.file-view-code .prose table` in index.css); jsdom neither loads index.css
 * nor performs layout, so this test locks the DOM contract the selectors
 * depend on — a bare marked table inside `.file-view-code > .prose`, plus
 * marked's `align` attributes, which the stylesheet restores after
 * @tailwindcss/typography resets cell alignment.
 *
 * Visual regression is covered by the manual smoke checklist in
 * docs/testing.md (Workspace & Panels).
 */

/** Exact file body from the issue's reproduction steps. */
const REPRO = [
  '## 3. 改动文件表',
  '',
  '| 文件 | 行锚 | 改动 | 量 |',
  '|---|---|---|---|',
  '| `frontend/src/components/workspace/WorkspaceForm.tsx` | 22-28 / 34-45 / 47-61 / 63-112 / 163-184 / 199-213 | 删 `suggestionsOpen`/`suggestionsRef` 与浮动下拉；加 `suggestionsLoading`；PREVIEW 区改为目录建议列表（含空态/加载态）；`onFocus` 空路径时预加载可信根；文案全部改 `t(\'workspaces.form.*\')` | 重写约 120 行 |',
  '| `frontend/src/i18n/locales/en/common.json` | `workspaces` 段 | 新增 `form` 子对象（11 个键） | +11 |',
  '| `frontend/src/i18n/locales/zh-CN/common.json` | `workspaces` 段 | 同上（中文） | +11 |',
  '| `frontend/tests/i18nForTests.ts` | `common` 最小 bundle | 补 `workspaces.new_workspace` + `workspaces.form.*`（测试最小英文资源） | +15 |',
  '| `frontend/tests/components/WorkspaceForm.suggestions.test.tsx` | 新增 | 组件回归（2 例：listbox 渲染+点选补全 / 聚焦预加载根目录） | +~90 |',
  '',
  '不改：后端、`useWorkspaceStore.ts`、`FormPage.tsx`、`CustomScrollbar.tsx`（P2 靠「列表改回 normal flow」规避，不改通用滚动组件，避免波及其他表单）。',
].join('\n');

/** GFM alignment markers, which marked emits as align="" attributes. */
const ALIGNED = [
  '| Left | Center | Right |',
  '| :--- | :---: | ---: |',
  '| a | b | c |',
].join('\n');

function renderMarkdownFile(content: string) {
  return render(
    <FileView content={content} fileType="markdown" filePath="test.md" />
  );
}

describe('FileView — GFM table structure (issue #41)', () => {
  it('renders the reported table as one table with one header and five body rows', () => {
    const { container } = renderMarkdownFile(REPRO);

    const prose = container.querySelector('.file-view-code > .prose');
    expect(prose).not.toBeNull();
    expect(container.querySelectorAll('table')).toHaveLength(1);

    const headers = Array.from(container.querySelectorAll('thead th')).map(
      (th) => th.textContent
    );
    expect(headers).toEqual(['文件', '行锚', '改动', '量']);

    const rows = container.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(5);
    expect(container.querySelectorAll('tbody td')).toHaveLength(20);

    const firstCell = rows[0].querySelector('td');
    expect(firstCell?.querySelector('code')?.textContent).toBe(
      'frontend/src/components/workspace/WorkspaceForm.tsx'
    );
  });

  it('keeps marked alignment attributes that the stylesheet restores', () => {
    const { container } = renderMarkdownFile(ALIGNED);

    const headers = container.querySelectorAll('thead th');
    expect(headers[0].getAttribute('align')).toBe('left');
    expect(headers[1].getAttribute('align')).toBe('center');
    expect(headers[2].getAttribute('align')).toBe('right');

    const cells = container.querySelectorAll('tbody td');
    expect(cells[0].getAttribute('align')).toBe('left');
    expect(cells[1].getAttribute('align')).toBe('center');
    expect(cells[2].getAttribute('align')).toBe('right');
  });
});
