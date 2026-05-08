import { useState, useMemo, useRef, useEffect } from 'react';
import type { Message, ContentBlock, ToolCall } from '@/types';
import { renderMarkdown } from '@/utils/markdown';

interface MessageRowProps {
  message: Message;
  messages?: Message[];
  toolCalls?: ToolCall[];
  isStreaming?: boolean;
  onFileOpen?: (path: string) => void;
}

// Tool icon mapping
const toolIcons: Record<string, string> = {
  bash: '⌨️',
  run_shell_command: '⌨️',
  kubectl: '☸️',
  read_file: '📄',
  write_file: '📝',
  edit_file: '✏️',
  glob: '🔍',
  grep: '🔎',
  list_files: '📁',
  web_search: '🌐',
  web_fetch: '🔗',
  web_click: '🖱️',
  browser_navigate: '🌐',
  browser_click: '🖱️',
  browser_type: '⌨️',
  browser_scroll: '📜',
  browser_screenshot: '📷',
  default: '🔧',
};

function getToolIcon(name: string): string {
  return toolIcons[name] || toolIcons.default;
}

// Format JSON value with syntax highlighting
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

// Parse and format JSON output - always show raw JSON
function JsonOutputDisplay({ content }: { content: string }) {
  try {
    const parsed = JSON.parse(content);
    // Always show the raw JSON with formatting
    return (
      <div className="json-output">
        <div className="json-tree">{formatJsonValue(parsed)}</div>
      </div>
    );
  } catch {
    // Not valid JSON, show as plain text
    return (
      <div className="json-output">
        <pre className="tool-result-pre">{content}</pre>
      </div>
    );
  }
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
export function ContextCompactionMessageRow({ message }: { message: Message }) {
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
}

// Reasoning message row - renders as an independent msg-row, collapsed by default
export function ReasoningMessageRow({ thinkingText }: { thinkingText: string }) {
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
}

// Extract reasoning/thinking text from a message
export function extractThinkingText(message: Message): string {
  let thinkingText = '';
  let content: string | ContentBlock[] = message.content || '';

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
  
  const icon = getToolIcon(toolName);
  
  // Get command/arguments from multiple sources
  let commandArgs: Record<string, unknown> | null = null;
  
  // 1. Try from toolCall.args first (most reliable)
  if (toolCall?.args && Object.keys(toolCall.args).length > 0) {
    commandArgs = toolCall.args;
  }
  
  // 2. Try from assistant message's tool_use block
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
  
  // 3. Try from message's own tool_calls (OpenAI format)
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
  
  return (
    <div className={`msg-row tool-message`}>
      <div 
        className="tool-message-header"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="msg-role tool">
          <div className="role-icon tool">{icon}</div>
          <span>{toolName}</span>
        </div>
        <span className="tool-card-toggle">{expanded ? '▼' : '▶'}</span>
      </div>
      {expanded && (
        <div className="msg-body">
          <div className="tool-card-section">
            <div className="tool-card-section-title">Param</div>
            {commandArgs ? (
              <div className="json-tree command-args">{formatJsonValue(commandArgs)}</div>
            ) : (
              <pre className="tool-card-command">(no command info)</pre>
            )}
          </div>
          <div className="tool-card-section">
            <div className="tool-card-section-title">Output</div>
            <JsonOutputDisplay content={content} />
          </div>
        </div>
      )}
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

export default function MessageRow({ message, messages = [], toolCalls = [], isStreaming }: MessageRowProps) {
  const roleClass = message.role === 'user' 
    ? 'msg-role user' 
    : message.role === 'tool'
    ? 'msg-role tool'
    : 'msg-role assistant';
    
  const roleText = message.role === 'user' 
    ? 'You' 
    : message.role === 'tool'
    ? 'Tool'
    : 'Hermes';

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

  // Extract stripped content (thinking/reasoning is rendered as separate ReasoningMessageRow)
  const { strippedContent } = useMemo(() => {
    let thinkingText = '';
    // Sprint 15: Handle null content
    let content: string | ContentBlock[] = message.content || '';

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
        <span dangerouslySetInnerHTML={{ __html: renderMarkdown(strippedContent as string) }} />
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
            dangerouslySetInnerHTML={{ __html: renderMarkdown(combinedText) }} 
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

  // Handle tool role messages separately - after all hooks are called
  if (message.role === 'tool') {
    return <ToolMessageRow message={message} messages={messages} toolCallMap={toolCallMap} />;
  }

  const msgBodyRef = useRef<HTMLDivElement>(null);

  // Inject copy buttons into <pre> elements after render
  useEffect(() => {
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
  });

  return (
    <div 
      className={`msg-row${message.role === 'user' ? ' user-message' : ''}`}
      data-raw-text={typeof message.content === 'string' ? message.content : ''}
    >
      <div className={roleClass}>
        <div className={`role-icon ${message.role}`}>
          {message.role === 'user' ? 'Y' : 'H'}
        </div>
        <span>{roleText}</span>
      </div>
      
      <div className="msg-body" ref={msgBodyRef}>
        {content}
        
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
    </div>
  );
}
