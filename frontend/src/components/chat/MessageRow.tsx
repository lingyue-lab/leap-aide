import React, { useState, useMemo, useRef, useEffect, memo } from 'react';
import type { Message, ContentBlock, ToolCall } from '@/types';
import { renderMarkdown } from '@/utils/markdown';

interface MessageRowProps {
  message: Message;
  messages?: Message[];
  toolCalls?: ToolCall[];
  isStreaming?: boolean;
  streaming?: boolean;         // true while any stream is active (for transient indicators)
  onFileOpen?: (path: string) => void;
  thinkingText?: string;
}

// ── Tool helpers ────────────────────────────────────────────────────────────────

/** Which argument key holds the primary display value for each tool. */
const TOOL_PRIMARY_ARG: Record<string, string> = {
  bash: 'command',
  run_shell_command: 'command',
  read_file: 'path',
  write_file: 'path',
  edit_file: 'path',
  glob: 'pattern',
  grep: 'pattern',
  list_files: 'path',
  web_search: 'query',
  web_fetch: 'url',
  kubectl: 'command',
  browser_navigate: 'url',
  browser_click: 'selector',
  browser_type: 'text',
  browser_console: 'expression',
  browser_screenshot: 'url',
  search_files: 'pattern',
  search_content: 'pattern',
};

/** Which tools always render output as pre/code instead of JSON tree. */
const TOOL_CODE_ARG: Record<string, string> = {
  bash: 'command',
  run_shell_command: 'command',
  kubectl: 'command',
};

/** Truncate a string to maxLen, appending "…" if truncated. */
function truncate(s: string, maxLen: number): string {
  if (!s || s.length <= maxLen) return s || '';
  return s.slice(0, maxLen) + '…';
}

/** Extract the most relevant argument value as a compact snippet for the header. */
function getCmdSnippet(toolName: string, args?: Record<string, unknown> | null): string {
  if (!args || Object.keys(args).length === 0) return '';
  const key = TOOL_PRIMARY_ARG[toolName];
  if (key && args[key] != null) {
    return truncate(String(args[key]), 80);
  }
  // Fallback: use the first string value
  for (const v of Object.values(args)) {
    if (typeof v === 'string' && v) return truncate(v, 80);
  }
  return '';
}

/** Format milliseconds as a human-readable duration. */
export function formatDuration(ms: number): string {
  if (ms == null || ms <= 0) return '';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const remS = Math.round(s % 60);
  return remS > 0 ? `${m}m ${remS}s` : `${m}m`;
}

// ── Tool sub-components ─────────────────────────────────────────────────────────
function formatJsonValue(value: unknown, depth: number = 0): React.ReactNode {
  if (value === null) {
    return <span className="json-null">null</span>;
  }
  if (value === undefined) {
    return <span className="json-undefined">undefined</span>;
  }
  if (typeof value === 'boolean') {
    return <span className="json-boolean">{value ? 'true' : 'false'}</span>;
  }
  if (typeof value === 'number') {
    return <span className="json-number">{value}</span>;
  }
  if (typeof value === 'string') {
    // For long strings with newlines, show them properly
    if (value.includes('\n')) {
      return (
        <span className="json-string json-string-long">
          "{value.split('\n').map((line, i, arr) => (
            <span key={i}>
              {line}
              {i < arr.length - 1 && <br />}
            </span>
          ))}"
        </span>
      );
    }
    if (value.length > 100) {
      return <span className="json-string json-string-long">"{value}"</span>;
    }
    return <span className="json-string">"{value}"</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="json-bracket">[]</span>;
    }
    return (
      <span className="json-array">
        <span className="json-bracket">[</span>
        <div className="json-indent">
          {value.map((item, idx) => (
            <div key={idx} className="json-item">
              {formatJsonValue(item, depth + 1)}
              {idx < value.length - 1 && <span className="json-comma">,</span>}
            </div>
          ))}
        </div>
        <span className="json-bracket">]</span>
      </span>
    );
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) {
      return <span className="json-bracket">{'{}'}</span>;
    }
    return (
      <span className="json-object">
        <span className="json-bracket">{'{'}</span>
        <div className="json-indent">
          {entries.map(([key, val], idx) => (
            <div key={key} className="json-entry">
              <span className="json-key">{key}</span>
              <span className="json-colon">:</span>
              <span className="json-value">{formatJsonValue(val, depth + 1)}</span>
              {idx < entries.length - 1 && <span className="json-comma">,</span>}
            </div>
          ))}
        </div>
        <span className="json-bracket">{'}'}</span>
      </span>
    );
  }
  return String(value);
}

// Check if a message is a context compaction reference message
export function isContextCompactionMessage(message: Message): boolean {
  if (message.role === 'tool') return false;
  const c = message.content || '';
  const text = typeof c === 'string'
    ? c
    : Array.isArray(c)
      ? (c as ContentBlock[]).filter(b => b.type === 'text').map(b => b.text || '').join('')
      : '';
  return /^\s*\[context compaction/i.test(text) || /^\s*context compaction/i.test(text);
}

// Context compaction reference card - collapsed by default
export const ContextCompactionMessageRow = memo(function ContextCompactionMessageRow({ message }: { message: Message }) {
  const [expanded, setExpanded] = useState(false);
  const c = message.content || '';
  const text = typeof c === 'string'
    ? c
    : Array.isArray(c)
      ? (c as ContentBlock[]).filter(b => b.type === 'text').map(b => b.text || '').join('')
      : '';
  const previewLines = text.split(/\n+/).filter(Boolean).slice(0, 2).join(' ');

  return (
    <div className="msg-row context-compaction-message">
      <div className="context-compaction-header" onClick={() => setExpanded(!expanded)}>
        <span className="context-compaction-icon">★</span>
        <span className="context-compaction-title">Context Compaction</span>
        <span className="context-compaction-preview">Reference Only · {previewLines}</span>
        <span className="reasoning-toggle">{expanded ? '▼' : '▶'}</span>
      </div>
      {expanded && (
        <div className="msg-body context-compaction-body">
          <pre className="context-compaction-pre">{text}</pre>
        </div>
      )}
    </div>
  );
});
const MessageRow = memo(MessageRowComponent);
MessageRow.displayName = 'MessageRow';

export default MessageRow;

// Reasoning message row - renders as an independent msg-row, collapsed by default
export const ReasoningMessageRow = memo(function ReasoningMessageRow({ thinkingText }: { thinkingText: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="msg-row reasoning-message">
      <div className="reasoning-header" onClick={() => setExpanded(!expanded)}>
        <div className="msg-role reasoning">
          <div className="role-icon reasoning">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z" />
              <path d="M10 21h4" />
              <path d="M9 17h6" />
            </svg>
          </div>
          <span>Reasoning</span>
        </div>
        <span className="reasoning-toggle">{expanded ? '▼' : '▶'}</span>
      </div>
      {expanded && (
        <div className="msg-body msg-reasoning-body">
          {thinkingText.split('<|reasoning_sep|>').map((block, idx) => (
            <div key={idx} className="reasoning-block">
              <div dangerouslySetInnerHTML={{ __html: renderMarkdown(block.trim()) }} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
});

// Inline reasoning block - rendered within a MessageRow's msg-body, collapsed by default
// No separate msg-row or separator; reasoning is part of the same message.
export const ReasoningBlock = memo(function ReasoningBlock({ thinkingText }: { thinkingText: string }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="reasoning-inline">
      <div className="reasoning-header-inline" onClick={() => setExpanded(!expanded)}>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z" />
          <path d="M10 21h4" />
          <path d="M9 17h6" />
        </svg>
        <span>Reasoning</span>
        <span className="reasoning-toggle">{expanded ? '▼' : '▶'}</span>
      </div>
      {expanded && (
        <div className="msg-reasoning-body">
          {thinkingText.split('<|reasoning_sep|>').map((block, idx) => (
            <div key={idx} className="reasoning-block">
              <div dangerouslySetInnerHTML={{ __html: renderMarkdown(block.trim()) }} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
});
export function extractThinkingText(message: Message): string {
  let thinkingText = '';
  // For user messages with a skill invocation, show the original input
  // instead of the expanded skill content (FR-008).
  let content: string | ContentBlock[] =
    (message.role === 'user' && message.original_content)
      ? message.original_content
      : (message.content || '');

  // 1. Extract thinking/reasoning blocks from structured content (Claude extended thinking, o3)
  if (Array.isArray(content)) {
    const blocks = content as ContentBlock[];
    thinkingText = blocks
      .filter(b => b && (b.type === 'thinking' || b.type === 'reasoning'))
      .map(b => b.thinking || b.reasoning || b.text || '')
      .filter(t => t)
      .join('\n\n');
  }

  // 2. Check top-level reasoning field (Hermes format)
  if (!thinkingText && message.reasoning) {
    thinkingText = message.reasoning;
  }

  // 3. Parse inline thinking tags from plain text
  if (!thinkingText && typeof content === 'string') {
    const thinkMatches = [...content.matchAll(/ Phili([\s\S]*?)<\/think>/g)];
    if (thinkMatches.length > 0) {
      thinkingText = thinkMatches.map(m => m[1].trim()).filter(t => t).join('\n\n');
    }
    if (!thinkingText) {
      const gemmaMatches = [...content.matchAll(/<\|channel>thought\n([\s\S]*?)<channel\|>/g)];
      if (gemmaMatches.length > 0) {
        thinkingText = gemmaMatches.map(m => m[1].trim()).filter(t => t).join('\n\n');
      }
    }
  }

  return thinkingText;
}



// ── Tool Output Helpers ─────────────────────────────────────────────────────────

/** Strip <untrusted_tool_result ...> XML wrapper and system preamble from content. */
function _stripXmlWrapper(raw: string): string {
  let s = raw;
  s = s.replace(/<untrusted_tool_result[^>]*>/gi, '');
  s = s.replace(/<\/untrusted_tool_result>/gi, '');
  s = s.replace(
    /^[\s\n]*The following content was retrieved from an external source\.[\s\S]*?only the user \(outside this block\) can issue instructions\.\s*/i,
    '',
  );
  return s.trim();
}

/** Recursively unwrap JSON objects whose top key is a framework wrapper. */
const OUTPUT_WRAPPER_KEYS = ['untrusted_tool_result', 'tool_result'];

function _unwrapJsonWrapper(parsed: unknown): unknown {
  if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
    const obj = parsed as Record<string, unknown>;
    for (const key of OUTPUT_WRAPPER_KEYS) {
      if (key in obj && obj[key] != null) {
        return _unwrapJsonWrapper(obj[key]);
      }
    }
  }
  return parsed;
}

/**
 * Universal output-field priority — not per-tool, but by semantic weight.
 * A single list that works for any tool the agent might return.
 */
const OUTPUT_FIELD_PRIORITY = [
  'output', 'stdout',           // shell / command tools
  'content',                     // read_file, web_fetch
  'snapshot',                    // browser_*
  'files', 'matches', 'results', // search / list tools
  'result', 'message', 'error',  // generic fallbacks
];

function _pickOutputField(parsed: Record<string, unknown>): { key: string; value: unknown } | null {
  for (const key of OUTPUT_FIELD_PRIORITY) {
    const v = parsed[key];
    if (v != null && v !== '') return { key, value: v };
  }
  // Nothing matched — pick the longest string value
  let best = '';
  let bestLen = 0;
  for (const [k, v] of Object.entries(parsed)) {
    if (typeof v === 'string' && v.length > bestLen) { best = k; bestLen = v.length; }
  }
  if (best) return { key: best, value: parsed[best] };
  return null;
}

function _renderOutputValue(value: unknown): React.ReactNode {
  if (typeof value === 'string') {
    const lines = value.split('\n').length;
    const body = <pre className="tool-result-pre">{value}</pre>;
    if (lines <= 30) return <div className="tool-output-block">{body}</div>;
    return (
      <div className="tool-output-block">
        <details className="tool-output-details" open>
          <summary className="tool-output-summary">{lines} lines</summary>
          {body}
        </details>
      </div>
    );
  }
  // Array of strings → list
  if (Array.isArray(value) && value.length > 0 && value.every((v) => typeof v === 'string')) {
    return (
      <div className="tool-output-block">
        <ul className="tool-output-list">
          {(value as string[]).map((item, i) => (
            <li key={i} className="tool-output-list-item">{item}</li>
          ))}
        </ul>
      </div>
    );
  }
  // Empty array / empty object
  if ((Array.isArray(value) && value.length === 0) ||
      (typeof value === 'object' && !Array.isArray(value) && Object.keys(value as object).length === 0)) {
    return <div className="tool-output-block"><span className="tool-output-waiting">(empty)</span></div>;
  }
  // Scalar 0 / false
  if (typeof value === 'number' || typeof value === 'boolean') {
    return <div className="tool-output-block"><span className="tool-output-scalar">{String(value)}</span></div>;
  }
  // Small scalar-only object → inline KV
  if (typeof value === 'object' && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.every(([, v]) => typeof v !== 'object' || v === null) && entries.length <= 5) {
      return (
        <div className="tool-output-block">
          <div className="tool-output-kv">
            {entries.map(([k, v]) => (
              <span key={k} className="tool-output-kv-item">
                <em>{k}</em> {String(v ?? '—')}
              </span>
            ))}
          </div>
        </div>
      );
    }
  }
  // Other → compact JSON tree
  const jsonText = JSON.stringify(value, null, 2);
  const lines = jsonText.split('\n').length;
  return (
    <div className="tool-output-block">
      {lines > 15 ? (
        <details className="tool-output-details">
          <summary className="tool-output-summary">JSON · {lines} lines</summary>
          <div className="json-tree">{formatJsonValue(value)}</div>
        </details>
      ) : (
        <div className="json-tree">{formatJsonValue(value)}</div>
      )}
    </div>
  );
}

// ── Tool Output Block ───────────────────────────────────────────────────────────

function ToolOutputBlock({ toolName, content }: { toolName: string; content: string }) {
  if (!content) {
    return <div className="tool-output-block"><span className="tool-output-waiting">Waiting for update...</span></div>;
  }

  // Step 1: strip XML wrapper
  const cleaned = _stripXmlWrapper(content);

  // Bash tools: raw output directly
  if (toolName in TOOL_CODE_ARG) {
    return _renderOutputValue(cleaned || content);
  }

  if (!cleaned) {
    return <div className="tool-output-block"><span className="tool-output-waiting">(empty)</span></div>;
  }

  // Step 2: try JSON → pick best field
  try {
    const parsed = JSON.parse(cleaned);
    const unwrapped = _unwrapJsonWrapper(parsed);

    if (typeof unwrapped === 'string') return _renderOutputValue(unwrapped);
    if (unwrapped == null || (typeof unwrapped === 'object' && Object.keys(unwrapped as object).length === 0)) {
      return <div className="tool-output-block"><span className="tool-output-waiting">(empty)</span></div>;
    }

    if (typeof unwrapped === 'object' && !Array.isArray(unwrapped)) {
      const picked = _pickOutputField(unwrapped as Record<string, unknown>);
      if (picked) {
        return _renderOutputValue(picked.value);
      }
    }

    return _renderOutputValue(unwrapped);
  } catch {
    return _renderOutputValue(cleaned);
  }
}

// ── Tool Command Block ──────────────────────────────────────────────────────────
// Same philosophy as output: show the meaningful value, not raw JSON.
//   Shell tools → $ command_string
//   Other tools → primary_arg_name: value
//   Full args JSON folded behind "All args" toggle.

function _renderArgValue(key: string, value: unknown): React.ReactNode {
  const s = typeof value === 'string' ? value : JSON.stringify(value);
  // Short value → inline
  if (s.length <= 120 && !s.includes('\n')) {
    return <span className="tool-cmd-arg-inline"><em>{key}</em>: {s}</span>;
  }
  // Long / multi-line → pre block
  return (
    <div className="tool-cmd-arg-block">
      <span className="tool-cmd-arg-key">{key}:</span>
      <pre className="tool-cmd-code">{s}</pre>
    </div>
  );
}

function ToolCommandBlock({ toolName, args }: { toolName: string; args?: Record<string, unknown> | null }) {
  const isShell = toolName in TOOL_CODE_ARG;
  const primaryKey = TOOL_PRIMARY_ARG[toolName];
  const primaryVal = primaryKey && args?.[primaryKey] != null ? args[primaryKey] : null;

  // Shell tools: show $ prompt + code block
  if (isShell && primaryVal) {
    return (
      <div className="tool-cmd-block">
        <div className="tool-cmd-prompt">$</div>
        <pre className="tool-cmd-code">{String(primaryVal)}</pre>
      </div>
    );
  }

  if (!args || Object.keys(args).length === 0) {
    return (
      <div className="tool-cmd-block">
        <pre className="tool-cmd-code">(no arguments)</pre>
      </div>
    );
  }

  // Pick primary arg, fallback to first entry
  const entries = Object.entries(args);
  const [showKey, showVal] = primaryVal != null
    ? [primaryKey!, primaryVal]
    : [entries[0][0], entries[0][1]];

  return (
    <div className="tool-cmd-block">
      {_renderArgValue(showKey, showVal)}
    </div>
  );
}

// ── Tool Message Row ────────────────────────────────────────────────────────────

function ToolMessageRow({ message, messages, toolCallMap }: { message: Message; messages: Message[]; toolCallMap: Map<string, ToolCall> }) {
  const [expanded, setExpanded] = useState(false);

  const content = typeof message.content === 'string'
    ? message.content
    : '';

  // Find the corresponding tool_use block from assistant messages
  const toolCallId = message.tool_call_id || '';
  const toolCall = toolCallMap.get(toolCallId);

  // Try to get tool name from multiple sources
  let toolName = toolCall?.name || message.tool_name || '';

  // If no name yet, try to find from assistant message's tool_use blocks
  if (!toolName) {
    for (const msg of messages) {
      if (msg.role !== 'assistant') continue;
      const blocks = msg.content;
      if (!Array.isArray(blocks)) continue;

      for (const block of blocks) {
        if (block.type === 'tool_use' && block.id === toolCallId) {
          toolName = block.name || '';
          break;
        }
      }
      if (toolName) break;
    }
  }

  // Also try OpenAI format assistant messages
  if (!toolName) {
    for (const msg of messages) {
      if (msg.role !== 'assistant') continue;
      const toolCalls = (msg as Message).tool_calls;
      if (!toolCalls) continue;

      for (const tc of toolCalls) {
        if (tc.id === toolCallId || tc.call_id === toolCallId) {
          toolName = tc.function?.name || '';
          break;
        }
      }
      if (toolName) break;
    }
  }

  // Fallback to Unknown
  if (!toolName) {
    toolName = 'Unknown';
  }

  // Get command/arguments from multiple sources
  let commandArgs: Record<string, unknown> | null = null;

  if (toolCall?.args && Object.keys(toolCall.args).length > 0) {
    commandArgs = toolCall.args;
  }

  if (!commandArgs) {
    for (const msg of messages) {
      if (msg.role !== 'assistant') continue;
      const blocks = msg.content;
      if (!Array.isArray(blocks)) continue;

      for (const block of blocks) {
        if (block.type === 'tool_use' && block.id === toolCallId) {
          if (block.input && Object.keys(block.input).length > 0) {
            commandArgs = block.input;
            break;
          }
        }
      }
      if (commandArgs) break;
    }
  }

  if (!commandArgs && message.tool_calls) {
    for (const tc of message.tool_calls) {
      if (tc.id === toolCallId || tc.call_id === toolCallId) {
        if (tc.function?.arguments) {
          if (typeof tc.function.arguments === 'string') {
            try {
              commandArgs = JSON.parse(tc.function.arguments);
            } catch {
              commandArgs = { arguments: tc.function.arguments };
            }
          } else {
            commandArgs = tc.function.arguments;
          }
          break;
        }
      }
    }
  }

  // Detect out-of-band user messages (steer) embedded in tool output
  const hasOOB = typeof content === 'string' && content.includes('OUT-OF-BAND USER MESSAGE');

  // Determine state
  const isRunning = message._live === true;
  const isError = toolCall?.is_error === true;
  const duration = toolCall?.duration;
  const cmdSnippet = getCmdSnippet(toolName, commandArgs);

  // Build class list — no per-tool running state: ThinkIndicator already shows it
  const classes = [
    'msg-row',
    'tool-message',
    hasOOB ? 'tool-message--steered' : '',
    isError ? 'tool-message--error' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={classes}>
      <div
        className="tool-message-header"
        onClick={() => setExpanded(!expanded)}
      >
        {/* Status indicator: ✓ complete, ✗ error; nothing while running (ThinkIndicator handles it) */}
        {!isRunning && (
          <span className={`tool-status ${isError ? 'error' : 'done'}`}>
            {isError ? '✗' : '✓'}
          </span>
        )}

        {/* Tool name */}
        <span className="tool-name">{toolName}</span>

        {/* Duration */}
        {!isRunning && duration != null && duration > 0 && (
          <span className="tool-duration">· {formatDuration(duration * 1000)}</span>
        )}

        {/* Command snippet */}
        {cmdSnippet && (
          <span className="tool-snippet">{truncate(cmdSnippet, 60)}</span>
        )}

        {hasOOB && (
          <span className="tool-steer-badge" title="This tool output contains a steered user message">↪ Steer</span>
        )}

        {/* Flex spacer */}
        <span style={{ flex: 1 }} />

        {/* Toggle */}
        <span className="tool-card-toggle">{expanded ? '▼' : '▶'}</span>
      </div>

      {expanded && (
        <div className="tool-message-body">
          <ToolCommandBlock toolName={toolName} args={commandArgs} />
          <ToolOutputBlock toolName={toolName} content={content} />
        </div>
      )}

      {(() => {
        const ts = message._ts ?? message.timestamp;
        if (!ts) return null;
        const d = new Date(ts * 1000);
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        const ss = String(d.getSeconds()).padStart(2, '0');
        return <div className="msg-timestamp">{hh}:{mm}:{ss}</div>;
      })()}
    </div>
  );
}

function fallbackCopy(text: string) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.top = '-9999px';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  try { document.execCommand('copy'); } catch { /* ignore */ }
  document.body.removeChild(ta);
}

function MessageRowComponent({ message, messages = [], toolCalls = [], isStreaming, streaming, thinkingText }: MessageRowProps) {
  // Build tool_call_id -> ToolCall mapping from session tool_calls
  const toolCallMap = useMemo(() => {
    const map = new Map<string, ToolCall>();
    for (const tc of toolCalls) {
      if (tc.tid) {
        map.set(tc.tid, tc);
      }
    }
    return map;
  }, [toolCalls]);

  // Extract stripped content (thinking/reasoning is rendered inline via ReasoningBlock)
  const { strippedContent } = useMemo(() => {
    let thinkingText = '';
    // Sprint 15: Handle null content
    let content: string | ContentBlock[] = (message.original_content as string) || message.content || '';

    // 1. Extract thinking/reasoning blocks from structured content (Claude extended thinking, o3)
    if (Array.isArray(content)) {
      const blocks = content as ContentBlock[];
      thinkingText = blocks
        .filter(b => b && (b.type === 'thinking' || b.type === 'reasoning'))
        .map(b => b.thinking || b.reasoning || b.text || '')
        .filter(t => t)
        .join('\n\n');
      // Keep only text blocks for content (tool_use/tool_result handled separately)
      content = blocks.filter(b => b && b.type === 'text');
    }

    // 2. Check top-level reasoning field (Hermes format)
    if (!thinkingText && message.reasoning) {
      thinkingText = message.reasoning;
    }

    // 3. Parse inline thinking tags from plain text: Phili... Phili (DeepSeek, QwQ, MiniMax, etc.)
    //    and Gemma 4 channel tokens: <|channel>thought\n...<channel|>
    if (!thinkingText && typeof content === 'string') {
      // Use global match for all Phili blocks
      const thinkMatches = [...content.matchAll(/ Phili([\s\S]*?)<\/think>/g)];
      if (thinkMatches.length > 0) {
        thinkingText = thinkMatches.map(m => m[1].trim()).filter(t => t).join('\n\n');
        content = content.replace(/ Phili[\s\S]*?<\/think>\s*/g, '').trimStart();
      }
      if (!thinkingText) {
        const gemmaMatches = [...content.matchAll(/<\|channel>thought\n([\s\S]*?)<channel\|>/g)];
        if (gemmaMatches.length > 0) {
          thinkingText = gemmaMatches.map(m => m[1].trim()).filter(t => t).join('\n\n');
          content = content.replace(/<\|channel>thought\n[\s\S]*?<channel\|>\s*/g, '').trimStart();
        }
      }
    }

    return { thinkingText, strippedContent: content };
  }, [message.content, message.reasoning]);

  // Throttle markdown rendering during streaming to avoid performance issues
  // Cache rendered HTML and only re-render at most every 150ms during streaming
  const mdCacheRef = useRef<{ content: string; html: string }>({ content: '', html: '' });
  const lastRenderTimeRef = useRef(0);
  
  const getRenderedMarkdown = (text: string): string => {
    // For non-streaming, always render fresh with highlighting
    if (!isStreaming) {
      return renderMarkdown(text, false);
    }
    
    // During streaming, use cached result if content hasn't changed
    if (mdCacheRef.current.content === text) {
      return mdCacheRef.current.html;
    }
    
    // Throttle: only re-render at most every 150ms during streaming
    const now = Date.now();
    if (now - lastRenderTimeRef.current < 150) {
      // Return cached result, but schedule a render for later
      return mdCacheRef.current.html || renderMarkdown(text, true);
    }
    
    lastRenderTimeRef.current = now;
    // Skip syntax highlighting during streaming for performance
    const html = renderMarkdown(text, true);
    mdCacheRef.current = { content: text, html };
    return html;
  };

  const content = useMemo(() => {
    // If original content was a string, use strippedContent (with thinking tags removed)
    if (typeof message.content === 'string') {
      // Sprint 15: Render even empty strings during streaming (shows placeholder)
      if (!message.content.trim() && !isStreaming) {
        return null;
      }
      if (!message.content.trim()) {
        return <span className="msg-placeholder">...</span>;
      }
      return (
        <span dangerouslySetInnerHTML={{ __html: getRenderedMarkdown(strippedContent as string) }} />
      );
    }
    
    // Handle multimodal/structured content
    // Sprint 15: Handle null content (can happen during streaming or on refresh)
    if (!message.content) {
      return null;
    }
    
    const blocks = message.content as ContentBlock[];
    const elements: React.ReactNode[] = [];
    let lastKey = 0;
    
    // First, collect all text blocks and render them together
    // Note: thinking/reasoning blocks are already extracted and shown separately
    const textBlocks = blocks.filter(b => b.type === 'text');
    if (textBlocks.length > 0) {
      // Combine text blocks and strip any inline thinking tags
      let combinedText = textBlocks.map(b => b.text || '').join('\n');
      // Remove Phili tags from display (they're shown in thinking card)
      combinedText = combinedText.replace(/ Phili[\s\S]*?<\/think>\s*/g, '').trimStart();
      combinedText = combinedText.replace(/<\|channel>thought\n[\s\S]*?<channel\|>\s*/g, '').trimStart();
      if (combinedText.trim()) {
        elements.push(
          <span 
            key={`text-${lastKey++}`} 
            dangerouslySetInnerHTML={{ __html: getRenderedMarkdown(combinedText) }} 
          />
        );
      }
    }
    
    // Note: tool_use and tool_result blocks are NOT rendered in msg-body.
    // They are displayed as separate role='tool' messages (ToolMessageRow),
    // which provides consistent display for both streaming and historical messages.
    
    // Handle image blocks
    blocks.forEach((block, idx) => {
      if (block.type === 'image' && block.source) {
        elements.push(
          <img
            key={`img-${idx}`}
            src={`data:${block.source.media_type};base64,${block.source.data}`}
            alt="Attached image"
            className="msg-media-img"
            loading="lazy"
          />
        );
      }
    });
    
    return elements.length > 0 ? <>{elements}</> : null;
  }, [message.content, message.role, isStreaming, toolCallMap, strippedContent]);

  const msgBodyRef = useRef<HTMLDivElement>(null);

  // Track content hash for effect dependency (avoid object reference issues)
  const contentHash = useMemo(() => {
    if (typeof message.content === 'string') {
      return message.content;
    }
    if (Array.isArray(message.content)) {
      return JSON.stringify(message.content);
    }
    return '';
  }, [message.content]);

  // Inject copy buttons into <pre> elements after render
  // Skip during streaming — content changes too rapidly for copy buttons to be useful
  useEffect(() => {
    if (isStreaming) return;
    const container = msgBodyRef.current;
    if (!container) return;
    const pres = container.querySelectorAll<HTMLElement>('pre[class*="language-"]');
    pres.forEach(pre => {
      if (pre.querySelector('.code-copy-btn')) return; // already added
      const code = pre.querySelector('code');
      if (!code) return;
      const btn = document.createElement('button');
      btn.className = 'code-copy-btn';
      btn.title = 'Copy';
      btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
      btn.addEventListener('click', () => {
        const text = code.textContent || '';
        const copyIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
        const checkIcon = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
        const onSuccess = () => {
          btn.innerHTML = checkIcon;
          btn.classList.add('copied');
          setTimeout(() => {
            btn.innerHTML = copyIcon;
            btn.classList.remove('copied');
          }, 2000);
        };
        // Clipboard API requires HTTPS; fall back to execCommand for HTTP
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
            fallbackCopy(text);
            onSuccess();
          });
        } else {
          fallbackCopy(text);
          onSuccess();
        }
      });
      pre.style.position = 'relative';
      pre.appendChild(btn);
    });
  }, [contentHash, isStreaming]); // Re-run when streaming finishes to inject copy buttons

  // Handle tool role messages separately - after all hooks are called
  if (message.role === 'tool') {
    return <ToolMessageRow message={message} messages={messages} toolCallMap={toolCallMap} />;
  }

  const isQueued = !!(message._queued);
  const isSteered = !!(message._steered);
  const queuePos = message._queue_position;
  // Steer→queue fallback: message has both flags
  const isSteerFallback = isQueued && isSteered;

  const isUser = message.role === 'user';

  const timestampNode = (() => {
    const ts = message._ts ?? message.timestamp;
    if (!ts) return null;
    const d = new Date(ts * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return <div className={`msg-timestamp${isUser ? ' msg-timestamp--user' : ''}`}>{hh}:{mm}:{ss}</div>;
  })();

  return (
    <>
      <div
        className={`msg-row${isUser ? ' user-message' : ''}${isQueued ? ' msg-row--queued' : ''}`}
        data-raw-text={typeof message.content === 'string' ? message.content : ''}
      >
        <div className="msg-body" ref={msgBodyRef}>
          {thinkingText && (
            <ReasoningBlock thinkingText={thinkingText} />
          )}
          {content}

          {isQueued && (
            <div className="msg-queued-indicator">
              <span className="msg-queued-icon">⏳</span>
              <span className="msg-queued-text">
                {isSteerFallback ? '排队中（注入失败）' : '排队中'}
              </span>
              {queuePos != null && (
                <span className="msg-queued-position">#{queuePos}</span>
              )}
            </div>
          )}

          {isSteered && !isQueued && streaming && (
            <div className="msg-steered-indicator">
              <span className="msg-steered-icon">↪</span>
              <span className="msg-steered-text">Steered（已注入到当前对话）</span>
            </div>
          )}

          {message.attachments && message.attachments.length > 0 && (
            <div className="msg-files">
              {message.attachments.map((file, idx) => (
                <div key={idx} className="msg-file-badge">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                  </svg>
                  <span>{file}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        {/* Assistant timestamp stays inside the row */}
        {!isUser && timestampNode}
      </div>
      {/* User timestamp sits outside the card */}
      {isUser && timestampNode}
    </>
  );
}
