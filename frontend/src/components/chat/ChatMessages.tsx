import React, { forwardRef, useEffect, useRef } from 'react';
import type { Message } from '@/types';
import MessageRow, { ReasoningMessageRow, extractThinkingText, isContextCompactionMessage, ContextCompactionMessageRow } from './MessageRow';
import { useChatStore } from '@/stores/useChatStore';

interface ChatMessagesProps {
  messages: Message[];
  streaming?: boolean;
  onFileOpen?: (path: string) => void;
}

const ChatMessages = forwardRef<HTMLDivElement, ChatMessagesProps>(
  ({ messages, streaming, onFileOpen }, ref) => {
    const innerRef = useRef<HTMLDivElement>(null);
    const scrollRef = ref || innerRef;
    const toolCalls = useChatStore((s) => s.toolCalls);

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
    useEffect(() => {
      if (!streaming || !_scrollPinned.current) return;
      const el = scrollRef as React.RefObject<HTMLDivElement>;
      if (!el.current) return;

      // Use requestAnimationFrame for smooth scrolling during streaming
      const scrollToBottom = () => {
        if (el.current) {
          el.current.scrollTo({
            top: el.current.scrollHeight,
            behavior: 'smooth'
          });
        }
      };

      // Scroll on next frame to ensure DOM has updated
      requestAnimationFrame(scrollToBottom);
    }, [
      // Track content and reasoning changes of the last assistant message during streaming
      streaming ? messages[messages.length - 1]?.content : null,
      streaming ? messages[messages.length - 1]?.reasoning : null,
      streaming
    ]);

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
          
          <div id="msgInner" style={{ maxWidth: '800px', margin: '0 auto', width: '100%', padding: '0 24px' }}>
            {(() => {
              // Sprint 15: Each tool message should be preceded by Hermes separator
              // to show that Hermes triggered the tool call
              const renderedIndices = new Set<number>();
              const result: React.ReactNode[] = [];
              
              for (let i = 0; i < messages.length; i++) {
                if (renderedIndices.has(i)) continue;
                
                const message = messages[i];
                
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
                  
                  // Render reasoning row if applicable
                  const thinkingText = extractThinkingText(message);
                  if (thinkingText) {
                    result.push(
                      <div key={`reasoning-sep-${i}`} className="msg-row hermes-separator reasoning-separator">
                        <div className="msg-role assistant">
                          <div className="role-icon assistant">H</div>
                          <span>Hermes</span>
                        </div>
                      </div>
                    );
                    result.push(
                      <ReasoningMessageRow
                        key={`reasoning-${i}`}
                        thinkingText={thinkingText}
                      />
                    );
                  }

                  // Render assistant message
                  result.push(
                    <MessageRow
                      key={`msg-${i}`}
                      message={message}
                      messages={messages}
                      toolCalls={toolCalls}
                      isStreaming={streaming && i === messages.length - 1}
                      onFileOpen={onFileOpen}
                    />
                  );
                  
                  // Collect following tool messages until next non-tool message
                  const isStreamingAssistant = streaming && i === messages.length - 1;
                  
                  for (let j = i + 1; j < messages.length; j++) {
                    const nextMsg = messages[j];
                    if (nextMsg.role !== 'tool') break;
                    
                    const tcId = nextMsg.tool_call_id || '';
                    if (isStreamingAssistant || toolCallIds.has(tcId) || toolCallIds.size === 0) {
                      renderedIndices.add(j);
                      // Sprint 15: Add Hermes separator before each tool message
                      result.push(
                        <div key={`hermes-sep-${j}`} className="msg-row hermes-separator">
                          <div className="msg-role assistant">
                            <div className="role-icon assistant">H</div>
                            <span>Hermes</span>
                          </div>
                        </div>
                      );
                      result.push(
                        <MessageRow
                          key={`msg-${j}`}
                          message={nextMsg}
                          messages={messages}
                          toolCalls={toolCalls}
                          isStreaming={false}
                          onFileOpen={onFileOpen}
                        />
                      );
                    } else {
                      break;
                    }
                  }
                } else if (message.role === 'tool') {
                  // Orphaned tool message (without preceding assistant)
                  renderedIndices.add(i);
                  // Add Hermes separator before orphaned tool message
                  result.push(
                    <div key={`hermes-sep-orphan-${i}`} className="msg-row hermes-separator">
                      <div className="msg-role assistant">
                        <div className="role-icon assistant">H</div>
                        <span>Hermes</span>
                      </div>
                    </div>
                  );
                  result.push(
                    <MessageRow
                      key={`msg-${i}`}
                      message={message}
                      messages={messages}
                      toolCalls={toolCalls}
                      isStreaming={false}
                      onFileOpen={onFileOpen}
                    />
                  );
                } else {
                  // Non-tool message (user)
                  renderedIndices.add(i);
                  result.push(
                    <MessageRow
                      key={`msg-${i}`}
                      message={message}
                      messages={messages}
                      toolCalls={toolCalls}
                      isStreaming={false}
                      onFileOpen={onFileOpen}
                    />
                  );
                }
              }
              
              return result;
            })()}
          </div>
        </div>
      </div>
    );
  }
);

ChatMessages.displayName = 'ChatMessages';

export default ChatMessages;
