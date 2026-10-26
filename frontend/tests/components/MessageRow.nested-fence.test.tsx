import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import MessageRow from '@/components/chat/MessageRow';
import type { Message } from '@/types';

/**
 * Visible-symptom regression test for issue #28: a ```markdown reply that
 * contains a ```bash block must render as ONE code box in the message row.
 *
 * The message body below is byte-identical to the assistant reply recorded in
 * state.db (session a1dca003c1d3, message 1555), the reply the issue was filed
 * against.
 */
const REPRO_CONTENT = [
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

describe('MessageRow — nested fenced code block (issue #28)', () => {
  it('renders the whole nested document inside a single <pre>', () => {
    const msg: Message = { role: 'assistant', content: REPRO_CONTENT };
    const { container } = render(
      <MessageRow message={msg} messages={[msg]} toolCalls={[]} />
    );

    expect(container.querySelectorAll('pre')).toHaveLength(1);

    const code = container.querySelector('pre');
    expect(code?.textContent).toContain('## Why');
    expect(code?.textContent).toContain('```bash');
    expect(code?.textContent).toContain('./scripts/run-test.sh frontend full');
    expect(code?.textContent).toContain('Ran locally on the branch');
    // The leaked tail would otherwise render as a real <p> outside the block.
    expect(container.querySelector('p')).toBeNull();
  });
});
