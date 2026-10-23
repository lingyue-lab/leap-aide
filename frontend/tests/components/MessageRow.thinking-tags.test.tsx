import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import MessageRow, { extractThinkingText } from '@/components/chat/MessageRow';
import type { Message } from '@/types';

/**
 * Regression tests for inline reasoning-tag handling in the message rows.
 *
 * The frontend extracts a *leading* `<think>…</think>` block into the
 * collapsible "reasoning" card and strips it from the visible body, mirroring
 * the backend's `_THINK_RE` (streaming.py) and the original static
 * `ui.js`/`messages.js` behaviour.
 *
 * A garbled literal (space + "Phili") had replaced the `<think>` opener, so
 * the block was never matched: reasoning leaked into the body and the
 * reasoning card stayed empty.
 *
 * The match is intentionally anchored to the start of the content: a literal
 * `<think>` mentioned mid-prose (e.g. in a code sample) must never be
 * stripped from the body.
 */
describe('MessageRow — inline <think> reasoning extraction', () => {
  it('extracts a leading <think>…</think> block into reasoning text', () => {
    const msg: Message = {
      role: 'assistant',
      content: '<think>let me reason about this</think>\nThe answer is 42.',
    };
    expect(extractThinkingText(msg)).toContain('let me reason about this');
  });

  it('extracts a leading <think> block even when preceded by whitespace', () => {
    const msg: Message = {
      role: 'assistant',
      content: '  \n<think>thinking here</think>\nanswer',
    };
    expect(extractThinkingText(msg)).toContain('thinking here');
  });

  it('does NOT treat a mid-content <think> literal as reasoning', () => {
    const msg: Message = {
      role: 'assistant',
      content: 'Use the <think>tag</think> like this in your prompt.',
    };
    expect(extractThinkingText(msg)).toBe('');
  });

  it('still extracts Gemma <|channel>thought blocks (control, already works)', () => {
    const msg: Message = {
      role: 'assistant',
      content: '<|channel>thought\nthinking here\n<channel|>\nanswer',
    };
    expect(extractThinkingText(msg)).toContain('thinking here');
  });
});

describe('MessageRow — reasoning body stripping', () => {
  it('does not leak a leading <think> block into the rendered body', () => {
    const msg: Message = {
      role: 'assistant',
      content: '<think>internal reasoning that must stay hidden</think>\nvisible answer',
    };
    const { container } = render(
      <MessageRow message={msg} messages={[msg]} toolCalls={[]} />
    );
    expect(container.textContent).toContain('visible answer');
    expect(container.textContent).not.toContain('internal reasoning');
  });

  it('keeps a mid-prose <think> literal in the body (no over-stripping)', () => {
    const msg: Message = {
      role: 'assistant',
      content: 'Write the tag as <think>example</think> in your prompt.',
    };
    const { container } = render(
      <MessageRow message={msg} messages={[msg]} toolCalls={[]} />
    );
    expect(container.textContent).toContain('example');
  });
});
