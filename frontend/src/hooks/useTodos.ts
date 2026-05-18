/**
 * useTodos
 *
 * Resolves the current todo list from chat state.
 *
 * Priority:
 *  1. Scan `messages` from the end for the most recent role="tool" message
 *     whose content is (or contains) a JSON object with a non-empty `todos` array.
 *     This is the fully-persisted state that arrives after streaming completes.
 *
 *  2. If no such message is found, fall back to `toolCalls` (the live streaming
 *     buffer).  The `write_todo` tool receives `todos` as an input argument,
 *     so the most recent call's `args.todos` reflects the latest todo state
 *     even while streaming is still in progress.
 */

import { useMemo } from 'react';
import { useChatStore } from '@/stores/useChatStore';
import type { Message, ContentBlock, ToolCall } from '@/types';

export interface TodoItem {
  id: string;
  content: string;
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled';
}

// ── Parser helpers ────────────────────────────────────────────────────────────

function _parseTodosFromString(s: string): TodoItem[] | null {
  try {
    const d = JSON.parse(s);
    if (d && Array.isArray(d.todos) && d.todos.length > 0) {
      return d.todos as TodoItem[];
    }
  } catch {
    // not JSON
  }
  return null;
}

/**
 * Try to extract todos from a single tool message.
 * Content can be a JSON string or a ContentBlock[].
 */
export function parseTodosFromMessage(msg: Message): TodoItem[] | null {
  const raw = msg.content;

  if (typeof raw === 'string') {
    return _parseTodosFromString(raw);
  }

  if (Array.isArray(raw)) {
    for (const block of raw as ContentBlock[]) {
      const text =
        block.type === 'tool_result'
          ? block.content
          : block.type === 'text'
          ? block.text
          : null;
      if (!text) continue;
      const result = _parseTodosFromString(text);
      if (result) return result;
    }
  }

  return null;
}

/**
 * Try to extract todos from live toolCalls (streaming fallback).
 * Looks for the most recent completed or in-flight `write_todo` call
 * and returns its `args.todos` array.
 */
function parseTodosFromToolCalls(toolCalls: ToolCall[]): TodoItem[] | null {
  // Iterate in reverse to find the latest write_todo / todo call
  for (let i = toolCalls.length - 1; i >= 0; i--) {
    const tc = toolCalls[i];
    if (tc.name !== 'write_todo' && tc.name !== 'todo') continue;
    const todos = tc.args?.todos;
    if (Array.isArray(todos) && todos.length > 0) {
      return todos as TodoItem[];
    }
  }
  return null;
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useTodos(): TodoItem[] {
  const messages = useChatStore((s) => s.messages);
  const toolCalls = useChatStore((s) => s.toolCalls);

  return useMemo<TodoItem[]>(() => {
    // 1. Prefer persisted tool message content (post-stream)
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.role !== 'tool') continue;
      const parsed = parseTodosFromMessage(msg);
      if (parsed) {
        return parsed;
      }
    }

    // 2. Fall back to live toolCalls args (during streaming)
    const fromCalls = parseTodosFromToolCalls(toolCalls);
    if (fromCalls) {
      return fromCalls;
    }

    return [];
  }, [messages, toolCalls]);
}
