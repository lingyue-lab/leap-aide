import React, { forwardRef, useEffect, useRef, useMemo } from 'react';
import type { Message, ToolCall } from '@/types';
import MessageRow, { extractThinkingText, isContextCompactionMessage, ContextCompactionMessageRow } from './MessageRow';
import { useChatStore } from '@/stores/useChatStore';
import { useSessionStore } from '@/stores/useSessionStore';
import ThinkingIndicator from './ThinkingIndicator';
import ConversationMinimap from './ConversationMinimap';

interface ChatMessagesProps {
  messages: Message[];
  streaming?: boolean;
  onFileOpen?: (path: string) => void;
}

// Stable selector: only re-render when toolCalls array reference changes
// Zustand's `set()` with same-reference check will avoid unnecessary re-renders
const selectToolCalls = (s: { toolCalls: ToolCall[] }) => s.toolCalls;

const ChatMessages = React.memo(forwardRef<HTMLDivElement, ChatMessagesProps>(
  ({ messages, streaming, onFileOpen }, ref) => {
    const innerRef = useRef<HTMLDivElement>(null);
    const scrollRef = ref || innerRef;
    const toolCalls = useChatStore(selectToolCalls);
    const showReasoning = useChatStore((s) => s.showReasoning);
    const phase = useChatStore((s) => s.phase);

    // Auto-scroll logic
    const _scrollPinned = useRef(true);

    useEffect(() => {
      const el = scrollRef as React.RefObject<HTMLDivElement>;
      if (!el.current) return;

      const handleScroll = () => {
        const container = el.current;
        if (!container) return;
        const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
        _scrollPinned.current = nearBottom;
      };

      el.current.addEventListener('scroll', handleScroll);
      return () => {
        el.current?.removeEventListener('scroll', handleScroll);
      };
    }, [scrollRef]);

    // Scroll to bottom on new messages if pinned
    useEffect(() => {
      if (!_scrollPinned.current) return;
      const el = scrollRef as React.RefObject<HTMLDivElement>;
      if (el.current) {
        el.current.scrollTop = el.current.scrollHeight;
      }
    }, [messages.length, scrollRef]);

    // Auto-scroll during streaming when content updates
    // Use a ref to track last content length to avoid triggering on every token
    const lastContentLenRef = useRef(0);
    useEffect(() => {
      if (!streaming || !_scrollPinned.current) return;
      const el = scrollRef as React.RefObject<HTMLDivElement>;
      if (!el.current) return;

      // Only scroll if content length changed by at least 50 chars (reduce thrashing)
      const lastMsg = messages[messages.length - 1];
      const contentLen = typeof lastMsg?.content === 'string' 
        ? lastMsg.content.length 
        : JSON.stringify(lastMsg?.content || '').length;
      
      if (Math.abs(contentLen - lastContentLenRef.current) < 50) return;
      lastContentLenRef.current = contentLen;

      // Use instant scroll during streaming to avoid thrashing from repeated smooth animations
      el.current.scrollTop = el.current.scrollHeight;
    }, [streaming, messages]);

    // Scroll to matching message when coming from search
    const matchMessageId = useSessionStore((s) => s.matchMessageId);
    const clearMatch = useSessionStore((s) => () => s.selectSession(s.currentSessionId, null));
    const lastMatchRef = useRef<number | null>(null);
    useEffect(() => {
      if (matchMessageId == null) return;
      // Already scrolled to this ID
      if (lastMatchRef.current === matchMessageId) return;
      // Check if target is in the current messages array (avoids DOM race with session switch)
      if (!messages.some(m => m.id === matchMessageId)) return;
      // Defer to next frame so React has rendered the messages
      requestAnimationFrame(() => {
        const el = (scrollRef as React.RefObject<HTMLDivElement>).current;
        if (!el) return;
        const target = el.querySelector(`[data-msg-id="${matchMessageId}"]`);
        if (!target) return;
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        target.classList.add('msg-search-highlight');
        setTimeout(() => target.classList.remove('msg-search-highlight'), 2000);
        lastMatchRef.current = matchMessageId;
        clearMatch();
      });
    }, [matchMessageId, messages]);

    // Messages are displayed in their original array order (the order they
    // appear in the session JSON). The backend appends messages in
    // chronological order, so no timestamp-based sorting is needed.
    // Sorting by timestamp is brittle — messages without timestamps (e.g.
    // added via CLI scripts) get pushed to epoch 0 and become invisible.
    const sortedMessages = messages;

    // Helper: get local date string "M月D日" from a unix timestamp (seconds)
    const getLocalDateLabel = (ts: number): string => {
      const d = new Date(ts * 1000);
      return `${d.getMonth() + 1}月${d.getDate()}日`;
    };

    // Helper: get local date key "YYYY-MM-DD" for deduplication
    const getLocalDateKey = (ts: number): string => {
      const d = new Date(ts * 1000);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    };

    return (
      <div
        ref={scrollRef}
        className="messages-container"
        onWheel={(e) => e.stopPropagation()}
        onTouchMove={(e) => e.stopPropagation()}
      >
        <div className="messages-inner">
          {messages.length === 0 && !streaming && (
            <div style={{ display: 'none' }} id="emptyState" />
          )}
          
          <div id="msgInner" style={{ width: '100%', padding: '0' }}>
            {useMemo(() => {
              // Sprint 15: Each tool message should be preceded by Hermes separator
              // to show that Hermes triggered the tool call
              const renderedIndices = new Set<number>();
              const result: React.ReactNode[] = [];
              let lastDateKey = '';
              
              for (let i = 0; i < sortedMessages.length; i++) {
                if (renderedIndices.has(i)) continue;
                
                const message = sortedMessages[i];
                const msgTs = message._ts ?? message.timestamp ?? 0;

                // Insert date separator when the day changes
                if (msgTs) {
                  const dateKey = getLocalDateKey(msgTs);
                  if (dateKey !== lastDateKey) {
                    lastDateKey = dateKey;
                    result.push(
                      <div key={`date-sep-${dateKey}`} className="msg-date-sep">
                        <span>{getLocalDateLabel(msgTs)}</span>
                      </div>
                    );
                  }
                }
                
                // For assistant messages, collect following tool messages
                if (message.role === 'assistant') {
                  renderedIndices.add(i);

                  // Check for context compaction message - render as collapsed card
                  if (isContextCompactionMessage(message)) {
                    result.push(
                      <ContextCompactionMessageRow
                        key={`compaction-${i}`}
                        message={message}
                      />
                    );
                    continue;
                  }
                  
                  // Collect tool_call_ids from this assistant message
                  const toolCallIds = new Set<string>();
                  
                  // OpenAI format: tool_calls at top level
                  if (message.tool_calls) {
                    for (const tc of message.tool_calls) {
                      toolCallIds.add(tc.id || tc.call_id || '');
                    }
                  }
                  
                  // Anthropic format: tool_use blocks in content
                  if (Array.isArray(message.content)) {
                    for (const block of message.content) {
                      if (block.type === 'tool_use' && block.id) {
                        toolCallIds.add(block.id);
                      }
                    }
                  }
                  
                  // Render reasoning row if applicable — pass thinkingText to MessageRow
                  // so reasoning is displayed inline within the same message (no separate separator)
                  const thinkingText = showReasoning ? extractThinkingText(message) : undefined;

                  // Render assistant message
                  result.push(
                    <div key={`msg-wrap-${i}`} data-msg-idx={i} data-msg-id={message.id}>
                      <MessageRow
                        key={`msg-${i}`}
                        message={message}
                        messages={sortedMessages}
                        toolCalls={toolCalls}
                        streaming={streaming}
                        isStreaming={streaming && i === sortedMessages.length - 1}
                        onFileOpen={onFileOpen}
                        thinkingText={thinkingText}
                      />
                    </div>
                  );
                  
                  // Collect following tool messages until next non-tool message
                  const isStreamingAssistant = streaming && i === sortedMessages.length - 1;
                  
                  for (let j = i + 1; j < sortedMessages.length; j++) {
                    const nextMsg = sortedMessages[j];
                    if (nextMsg.role !== 'tool') break;
                    
                    const tcId = nextMsg.tool_call_id || '';
                    if (isStreamingAssistant || toolCallIds.has(tcId) || toolCallIds.size === 0) {
                      renderedIndices.add(j);
                      result.push(
                        <div key={`msg-wrap-${j}`} data-msg-idx={j} data-msg-id={nextMsg.id}>
                          <MessageRow
                            key={`msg-${j}`}
                            message={nextMsg}
                            messages={sortedMessages}
                            toolCalls={toolCalls}
                        streaming={streaming}
                            isStreaming={false}
                            onFileOpen={onFileOpen}
                          />
                        </div>
                      );
                    } else {
                      break;
                    }
                  }
                } else if (message.role === 'tool') {
                  // Orphaned tool message (without preceding assistant)
                  renderedIndices.add(i);
                  result.push(
                    <div key={`msg-wrap-${i}`} data-msg-idx={i} data-msg-id={message.id}>
                      <MessageRow
                        key={`msg-${i}`}
                        message={message}
                        messages={sortedMessages}
                        toolCalls={toolCalls}
                        streaming={streaming}
                        isStreaming={false}
                        onFileOpen={onFileOpen}
                      />
                    </div>
                  );
                } else {
                  // Non-tool message (user)
                  renderedIndices.add(i);
                  const isUser = message.role === 'user';
                  result.push(
                    <div
                      key={`msg-wrap-${i}`}
                      data-msg-idx={i}
                      data-msg-id={message.id}
                      {...(isUser ? { 'data-user-msg-idx': i } : {})}
                    >
                      <MessageRow
                        key={`msg-${i}`}
                        message={message}
                        messages={sortedMessages}
                        toolCalls={toolCalls}
                        streaming={streaming}
                        isStreaming={false}
                        onFileOpen={onFileOpen}
                      />
                    </div>
                  );
                }
              }
              
              return result;
            }, [sortedMessages, streaming, toolCalls, onFileOpen])}
          </div>

          {/* Phase indicator — shown after sending, before first assistant token, and between tool calls */}
          {streaming && phase && (
            <ThinkingIndicator phase={phase} />
          )}
        </div>

        {/* Conversation minimap — right-edge navigation rail */}
        <ConversationMinimap
          messages={sortedMessages}
          containerRef={scrollRef as React.RefObject<HTMLDivElement>}
        />
      </div>
    );
  }));

ChatMessages.displayName = 'ChatMessages';

export default ChatMessages;
