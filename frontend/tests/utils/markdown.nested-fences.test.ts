import { describe, it, expect } from 'vitest';
import { renderMarkdown, repairNestedFenceDocument } from '@/utils/markdown';

/**
 * Regression tests for issue #28: a fenced markdown document that itself
 * contains a fenced code block was rendered as two parts.
 *
 * The real reply (state.db session a1dca003c1d3, message 1555) is a
 * ```markdown block whose body contains a ```bash block. CommonMark does not
 * nest fences: the inner bare ``` terminates the outer block, so everything
 * after the command leaks out as prose and the trailing ``` becomes a stray
 * empty code block.
 */

/** Exact assistant message from the reproduction (issue #28). */
const REPRO = [
  '```markdown',
  '## Why',
  '',
  '```bash',
  './scripts/run-test.sh frontend full',
  '```',
  '',
  'Ran locally on the branch: `vitest run` → 14 files / 85 tests passed.',
  '```',
].join('\n');

/** Same document, but the wrapper uses four backticks — CommonMark-legal. */
const FOUR_BACKTICKS = [
  '````markdown',
  '## Why',
  '',
  '```bash',
  './scripts/run-test.sh frontend full',
  '```',
  '',
  'Ran locally on the branch: `vitest run` → 14 files / 85 tests passed.',
  '````',
].join('\n');

/** Normal message: one closed fenced block followed by prose. */
const BLOCK_THEN_PROSE = [
  '```bash',
  'ls -la',
  '```',
  '',
  'That lists every file.',
].join('\n');

/** Normal message: two independent closed blocks with prose in between. */
const TWO_BLOCKS = [
  '```bash',
  'ls',
  '```',
  '',
  'Then:',
  '',
  '```bash',
  'pwd',
  '```',
].join('\n');

/** Malformed message that must NOT be silently merged: no nested opener. */
const DANGLING_WITHOUT_NESTING = [
  '```markdown',
  '# Title',
  '```',
  '',
  'And here is the closing fence:',
  '```',
].join('\n');

/** Wrapper nested in a list item still keeps its indentation after repair. */
const INDENTED = [
  '  ```markdown',
  '  ## Why',
  '',
  '  ```bash',
  '  ls',
  '  ```',
  '',
  '  tail',
  '  ```',
].join('\n');

/** Text of the first fenced block, with markup stripped. */
function firstBlockText(html: string): string {
  const m = /<pre class="language-([^"]*)"><code[^>]*>([\s\S]*?)<\/code><\/pre>/.exec(html);
  if (!m) return '';
  return m[2]
    .replace(/<[^>]+>/g, '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

describe('renderMarkdown — nested fenced code blocks (issue #28)', () => {
  it('keeps a ```markdown wrapper with an inner ```bash block in ONE code block', () => {
    const html = renderMarkdown(REPRO);

    expect((html.match(/<pre class="language-/g) || []).length).toBe(1);
    expect(html).toContain('<pre class="language-markdown">');

    const code = firstBlockText(html);
    expect(code).toContain('## Why');
    expect(code).toContain('```bash');
    expect(code).toContain('./scripts/run-test.sh frontend full');
    expect(code).toContain('Ran locally on the branch: `vitest run` → 14 files / 85 tests passed.');

    // Nothing leaked into prose, no inline-code/bold rendering of the tail.
    expect(html).not.toMatch(/<p>[^<]*Ran locally/);
    expect(html).not.toContain('<strong>14 files / 85 tests passed</strong>');
  });

  it('repairNestedFenceDocument rewrites the wrapper fence, content untouched', () => {
    const repaired = repairNestedFenceDocument(REPRO);
    const lines = repaired.split('\n');

    expect(lines[0]).toBe('````markdown');
    expect(lines[lines.length - 1]).toBe('````');
    expect(lines.slice(1, -1)).toEqual(REPRO.split('\n').slice(1, -1));
  });

  it('leaves an already-legal four-backtick wrapper alone', () => {
    const html = renderMarkdown(FOUR_BACKTICKS);
    expect((html.match(/<pre class="language-/g) || []).length).toBe(1);
    expect(firstBlockText(html)).toContain('Ran locally on the branch');
    expect(repairNestedFenceDocument(FOUR_BACKTICKS)).toBe(FOUR_BACKTICKS);
  });

  it('does not touch a closed block followed by prose', () => {
    const html = renderMarkdown(BLOCK_THEN_PROSE);
    expect((html.match(/<pre class="language-/g) || []).length).toBe(1);
    expect(html).toMatch(/<p>That lists every file\.<\/p>/);
    expect(repairNestedFenceDocument(BLOCK_THEN_PROSE)).toBe(BLOCK_THEN_PROSE);
  });

  it('does not merge two independent code blocks', () => {
    const html = renderMarkdown(TWO_BLOCKS);
    expect((html.match(/<pre class="language-/g) || []).length).toBe(2);
    expect(repairNestedFenceDocument(TWO_BLOCKS)).toBe(TWO_BLOCKS);
  });

  it('does not swallow prose when the trailing fence is dangling without nesting', () => {
    const html = renderMarkdown(DANGLING_WITHOUT_NESTING);
    expect(repairNestedFenceDocument(DANGLING_WITHOUT_NESTING)).toBe(DANGLING_WITHOUT_NESTING);
    expect(html).toMatch(/<p>And here is the closing fence:/);
  });
});

describe('repairNestedFenceDocument — indentation', () => {
  it('preserves the wrapper indentation', () => {
    const lines = repairNestedFenceDocument(INDENTED).split('\n');
    expect(lines[0]).toBe('  ````markdown');
    expect(lines[lines.length - 1]).toBe('  ````');
  });
});
