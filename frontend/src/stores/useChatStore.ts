import { create } from 'zustand';
import type { Message, ToolCall, UsageInfo, ContentBlock } from '@/types';

interface ChatState {
  messages: Message[];
  toolCalls: ToolCall[];  // Keep for backward compatibility, but prefer messages
  busy: boolean;
  activeStreamId: string | null;
  pendingFiles: File[];
  lastUsage: UsageInfo | null;
  abortController: AbortController | null;
  
  // Actions
  setMessages: (messages: Message[]) => void;
  addMessage: (message: Message) => void;
  updateLastMessage: (content: string | Message['content'], reasoning?: string) => void;
  appendToLastMessage: (token: string) => void;
  appendReasoning: (text: string) => void;
  // New: Append content block to last assistant message
  appendContentBlock: (block: ContentBlock) => void;
  // New: Update last tool message (role='tool')
  updateToolMessage: (toolCallId: string, updates: Partial<Message>) => void;
  setToolCalls: (toolCalls: ToolCall[]) => void;
  addToolCall: (toolCall: ToolCall) => void;
  updateToolCall: (tid: string, updates: Partial<ToolCall>) => void;
  setBusy: (busy: boolean) => void;
  setActiveStreamId: (id: string | null) => void;
  addPendingFile: (file: File) => void;
  removePendingFile: (index: number) => void;
  clearPendingFiles: () => void;
  setLastUsage: (usage: UsageInfo | null) => void;
  setAbortController: (controller: AbortController | null) => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  toolCalls: [],
  busy: false,
  activeStreamId: null,
  pendingFiles: [],
  lastUsage: null,
  abortController: null,

  setMessages: (messages) => set({ messages, toolCalls: [] }),

  addMessage: (message) => set((state) => ({
    messages: [...state.messages, message],
  })),

  updateLastMessage: (content, reasoning) => set((state) => {
    const messages = [...state.messages];
    if (messages.length > 0) {
      const lastMessage = messages[messages.length - 1];
      messages[messages.length - 1] = {
        ...lastMessage,
        content,
        reasoning: reasoning ?? lastMessage.reasoning,
      };
    }
    return { messages };
  }),

  appendToLastMessage: (token) => set((state) => {
    const messages = [...state.messages];
    // Sprint 15: Find the last assistant message (may not be the last message if tool messages exist)
    // Always append to the newest assistant message to handle multiple tool call cycles
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') {
        const currentContent = messages[i].content;
        if (typeof currentContent === 'string') {
          // Simple string content: append token directly
          messages[i] = {
            ...messages[i],
            content: currentContent + token,
            _live: true,
          };
        } else if (Array.isArray(currentContent)) {
          // ContentBlock[] content: append token to the last text block
          // This preserves tool_use blocks and other structured content
          const blocks = [...currentContent as ContentBlock[]];
          // Find the last text block to append to
          let lastTextIdx = -1;
          for (let j = blocks.length - 1; j >= 0; j--) {
            if (blocks[j].type === 'text') {
              lastTextIdx = j;
              break;
            }
          }
          if (lastTextIdx >= 0) {
            // Append to existing text block
            blocks[lastTextIdx] = {
              ...blocks[lastTextIdx],
              text: (blocks[lastTextIdx].text || '') + token,
            };
          } else {
            // No text block found — create a new one
            blocks.push({ type: 'text', text: token });
          }
          messages[i] = {
            ...messages[i],
            content: blocks,
            _live: true,
          };
        } else {
          // Null/undefined content — start as string
          messages[i] = {
            ...messages[i],
            content: token,
            _live: true,
          };
        }
        break;
      }
    }
    return { messages };
  }),

  appendReasoning: (text) => set((state) => {
    const messages = [...state.messages];
    // Sprint 15: Find the last assistant message (may not be the last message)
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') {
        const currentReasoning = messages[i].reasoning || '';
        messages[i] = {
          ...messages[i],
          reasoning: currentReasoning + text,
          _live: true,
        };
        break;
      }
    }
    return { messages };
  }),

  appendContentBlock: (block) => set((state) => {
    const messages = [...state.messages];
    // Sprint 15: Find the last assistant message (may not be the last message)
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') {
        const currentContent = messages[i].content;
        let newContent: ContentBlock[];
        if (Array.isArray(currentContent)) {
          newContent = [...currentContent, block];
        } else if (typeof currentContent === 'string') {
          // Convert string to ContentBlock array
          newContent = currentContent 
            ? [{ type: 'text', text: currentContent }, block]
            : [block];
        } else {
          newContent = [block];
        }
        messages[i] = {
          ...messages[i],
          content: newContent,
          _live: true,
        };
        break;
      }
    }
    return { messages };
  }),

  updateToolMessage: (toolCallId, updates) => set((state) => {
    const messages = state.messages.map((msg) => {
      if (msg.role === 'tool' && msg.tool_call_id === toolCallId) {
        return { ...msg, ...updates };
      }
      return msg;
    });
    return { messages };
  }),

  setToolCalls: (toolCalls) => set({ toolCalls }),

  addToolCall: (toolCall) => set((state) => ({
    toolCalls: [...state.toolCalls, toolCall],
  })),

  updateToolCall: (tid, updates) => set((state) => ({
    toolCalls: state.toolCalls.map((tc) =>
      tc.tid === tid ? { ...tc, ...updates } : tc
    ),
  })),

  setBusy: (busy) => set({ busy }),

  setActiveStreamId: (id) => set({ activeStreamId: id }),

  addPendingFile: (file) => set((state) => ({
    pendingFiles: [...state.pendingFiles, file],
  })),

  removePendingFile: (index) => set((state) => ({
    pendingFiles: state.pendingFiles.filter((_, i) => i !== index),
  })),

  clearPendingFiles: () => set({ pendingFiles: [] }),

  setLastUsage: (usage) => set({ lastUsage: usage }),

  setAbortController: (controller) => set({ abortController: controller }),

  reset: () => set({
    messages: [],
    toolCalls: [],
    busy: false,
    activeStreamId: null,
    pendingFiles: [],
    lastUsage: null,
    abortController: null,
  }),
}));
