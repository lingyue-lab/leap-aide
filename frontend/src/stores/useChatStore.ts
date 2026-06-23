import { create } from 'zustand';
import type { Message, ToolCall, UsageInfo, ContentBlock, QueuedMessage } from '@/types';

interface SessionCache {
  messages: Message[];
  toolCalls: ToolCall[];
  busy: boolean;
  activeStreamId: string | null;
  lastUsage: UsageInfo | null;
  queuedMessages: QueuedMessage[];
}

interface ChatState {
  messages: Message[];
  toolCalls: ToolCall[];  // Keep for backward compatibility, but prefer messages
  busy: boolean;
  activeStreamId: string | null;
  pendingFiles: File[];
  lastUsage: UsageInfo | null;
  abortController: AbortController | null;
  queuedMessages: QueuedMessage[];
  // Per-session state cache to preserve data across session switches
  _sessionCache: Map<string, SessionCache>;
  // Current session ID tracked by chat store (to know which cache slot to use)
  _currentSessionId: string | null;

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
  // Queue management
  addQueuedMessage: (msg: QueuedMessage) => void;
  removeQueuedMessage: (position: number) => void;
  clearQueuedMessages: () => void;
  /** Shift queue when front message is consumed by backend */
  promoteQueuedMessages: () => void;
  // Session cache actions
  saveToCache: (sessionId: string) => void;
  restoreFromCache: (sessionId: string) => SessionCache | null;
  clearCache: (sessionId: string) => void;
  setCurrentSessionId: (id: string | null) => void;
  /**
   * Directly overwrite the cached messages for a background session.
   * Called by StreamManager subscriber when the active session changes.
   */
  updateSessionCache: (sessionId: string, patch: Partial<SessionCache>) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  toolCalls: [],
  busy: false,
  activeStreamId: null,
  pendingFiles: [],
  lastUsage: null,
  abortController: null,
  queuedMessages: [],
  _sessionCache: new Map<string, SessionCache>(),
  _currentSessionId: null,

  setMessages: (messages) => {
    return set({ messages, toolCalls: [] });
  },

  addMessage: (message) => {
    return set((state) => ({
      messages: [...state.messages, message],
    }));
  },

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

  appendToLastMessage: (token) => {
    return set((state) => {
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
    });
  },

  appendReasoning: (text) => {
    return set((state) => {
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
    });
  },

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

  // ── Queue management ─────────────────────────────────────────────────────────
  addQueuedMessage: (msg) => set((state) => {
    return { queuedMessages: [...state.queuedMessages, msg] };
  }),

  removeQueuedMessage: (position) => set((state) => {
    return { queuedMessages: state.queuedMessages.filter((qm) => qm.position !== position) };
  }),

  clearQueuedMessages: () => {
    set({ queuedMessages: [] });
  },

  promoteQueuedMessages: () => set((state) => {
    if (state.queuedMessages.length === 0) return {};
    // Remove the front message and shift remaining positions
    const shifted = state.queuedMessages.slice(1).map((qm, i) => ({
      ...qm,
      position: i + 1,
    }));
    return { queuedMessages: shifted };
  }),

  reset: () => set({
    messages: [],
    toolCalls: [],
    busy: false,
    activeStreamId: null,
    pendingFiles: [],
    lastUsage: null,
    abortController: null,
    queuedMessages: [],
  }),

  // Save current chat state into per-session cache
  saveToCache: (sessionId: string) => set((state) => {
    const cache: SessionCache = {
      messages: state.messages,
      toolCalls: state.toolCalls,
      busy: state.busy,
      activeStreamId: state.activeStreamId,
      lastUsage: state.lastUsage,
      queuedMessages: state.queuedMessages,
    };
    const newCache = new Map(state._sessionCache);
    newCache.set(sessionId, cache);
    return { _sessionCache: newCache };
  }),

  // Restore chat state from per-session cache (returns null if no cache exists)
  restoreFromCache: (sessionId: string): SessionCache | null => {
    const state = useChatStore.getState() as ChatState;
    const cache = state._sessionCache.get(sessionId) as SessionCache | undefined;
    if (cache) {
      // Auto-clean stale queuedMessages when restoring from cache:
      // if the cached queue is non-empty but there are no actual ghost
      // messages, the queue is stale and should be cleared.
      let queuedToRestore = cache.queuedMessages || [];
      if (queuedToRestore.length > 0) {
        const cachedGhosts = (cache.messages || []).filter(m => m._queued);
        if (cachedGhosts.length === 0) {
          queuedToRestore = [];
        }
      }
      set({
        messages: cache.messages,
        toolCalls: cache.toolCalls,
        busy: cache.busy,
        activeStreamId: cache.activeStreamId,
        lastUsage: cache.lastUsage,
        queuedMessages: queuedToRestore,
        _currentSessionId: sessionId,
      });
      return cache;
    }
    return null;
  },

  // Clear cache for a specific session (e.g., after stream completes and data is loaded from backend)
  clearCache: (sessionId: string) => set((state) => {
    const newCache = new Map(state._sessionCache);
    newCache.delete(sessionId);
    return { _sessionCache: newCache };
  }),

  setCurrentSessionId: (id: string | null) => set({ _currentSessionId: id }),

  // Patch the cache for a background session without affecting current display
  updateSessionCache: (sessionId: string, patch: Partial<SessionCache>) => set((state) => {
    const existing = state._sessionCache.get(sessionId);
    const updated: SessionCache = {
      messages: existing?.messages ?? [],
      toolCalls: existing?.toolCalls ?? [],
      busy: existing?.busy ?? false,
      activeStreamId: existing?.activeStreamId ?? null,
      lastUsage: existing?.lastUsage ?? null,
      queuedMessages: existing?.queuedMessages ?? [],
      ...patch,
    };
    const newCache = new Map(state._sessionCache);
    newCache.set(sessionId, updated);
    // If this session is currently displayed, also update live state
    if (state._currentSessionId === sessionId) {
      // Only include fields that actually changed to minimize React re-renders
      // NOTE: queuedMessages is NOT synced here — it's managed exclusively
      // through addQueuedMessage / removeQueuedMessage / clearQueuedMessages.
      const liveUpdate: Partial<ChatState> = { _sessionCache: newCache };
      if (updated.messages !== state.messages) {
        // Preserve optimistically-added messages that StreamManager doesn't
        // know about: queued ghosts (⏳) and steered messages (↪).
        // Both are front-end-only markers and would be lost on sync.
        const preservedMessages = state.messages.filter(m => m._queued || m._steered);
        if (preservedMessages.length > 0) {
          liveUpdate.messages = [...updated.messages, ...preservedMessages];
        } else {
          liveUpdate.messages = updated.messages;
        }
      }
      if (updated.toolCalls !== state.toolCalls) liveUpdate.toolCalls = updated.toolCalls;
      if (updated.busy !== state.busy) liveUpdate.busy = updated.busy;
      if (updated.activeStreamId !== state.activeStreamId) liveUpdate.activeStreamId = updated.activeStreamId;
      if (updated.lastUsage !== state.lastUsage) liveUpdate.lastUsage = updated.lastUsage;
      return liveUpdate;
    }
    return { _sessionCache: newCache };
  }),
}));
