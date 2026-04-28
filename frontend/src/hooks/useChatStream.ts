import { useCallback, useRef } from 'react';
import { chatService } from '@/services/api';
import { useChatStore } from '@/stores/useChatStore';
import { useSessionStore } from '@/stores/useSessionStore';
import type { ContentBlock } from '@/types';

export function useChatStream() {
  const eventSourceRef = useRef<EventSource | null>(null);
  
  const {
    addMessage,
    appendToLastMessage,
    appendReasoning,
    appendContentBlock,
    updateToolMessage,
    addToolCall,
    updateToolCall,
    setMessages,
    setToolCalls,
    setBusy,
    setAbortController,
    setLastUsage,
    messages,
  } = useChatStore();
  
  const { updateCurrentSession, loadSession } = useSessionStore();

  const startStream = useCallback(async (
    sessionId: string,
    message: string,
    model: string,
    workspace?: string,
    attachments?: string[]
  ) => {
    // Create abort controller
    const abortController = new AbortController();
    setAbortController(abortController);
    
    // Add user message
    addMessage({
      role: 'user',
      content: message,
      attachments,
      _ts: Date.now() / 1000,
    });
    
    // Add placeholder assistant message
    addMessage({
      role: 'assistant',
      content: '',
      _ts: Date.now() / 1000,
      _live: true,
    });
    
    setBusy(true);

    try {
      // Start chat
      const { stream_id } = await chatService.startChat(
        sessionId,
        message,
        model,
        workspace,
        attachments
      );
      
      useChatStore.getState().setActiveStreamId(stream_id);

      // Create SSE connection
      const es = chatService.createSSEConnection(stream_id);
      eventSourceRef.current = es;

      // Handle events
      es.addEventListener('token', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        appendToLastMessage(data.text);
      });

      es.addEventListener('reasoning', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        appendReasoning(data.text);
      });

      // Tool call starts - add tool_use block to last assistant message
      es.addEventListener('tool', (e: MessageEvent) => {
        const data = JSON.parse(e.data) as { name: string; preview?: string; args?: Record<string, unknown>; tid?: string };
        if (data.name === 'clarify') return;
        
        // Use tid from backend, or generate a fallback
        const tid = data.tid || `live-${data.name}-${Date.now()}`;
        
        console.log('[SSE] tool event:', { name: data.name, tid, args: data.args });
        
        // Add tool_use block to the last assistant message
        const toolUseBlock: ContentBlock = {
          type: 'tool_use',
          id: tid,
          name: data.name,
          input: data.args || {},
        };
        appendContentBlock(toolUseBlock);
        
        // Sprint 15: Also update toolCalls store for immediate display
        addToolCall({
          name: data.name,
          tid: tid,
          args: data.args || {},
          snippet: '',
          done: false,
          preview: data.preview || '',
        });
        
        // Add a placeholder tool message (role='tool') for the result
        addMessage({
          role: 'tool',
          content: '',
          tool_call_id: tid,
          tool_name: data.name,
          _ts: Date.now() / 1000,
          _live: true,
        });
      });

      // Tool call completes - update the tool message with result
      es.addEventListener('tool_complete', (e: MessageEvent) => {
        const data = JSON.parse(e.data) as { name: string; tid?: string; output?: string; preview?: string; duration?: number; is_error?: boolean };
        if (data.name === 'clarify') return;
        
        console.log('[SSE] tool_complete event full:', JSON.stringify(data, null, 2));
        
        // tid should be provided by backend
        const tid = data.tid;
        if (!tid) {
          console.warn('[SSE] tool_complete event missing tid');
          return;
        }
        
        // Get output from the event data
        const output = data.output || data.preview || '';
        
        console.log('[SSE] updating tool message:', { tid, output: output.substring(0, 100) + '...' });
        
        // Update the tool message with the result
        updateToolMessage(tid, {
          content: output,
          _live: false,
        });
        
        // Sprint 15: Also update toolCalls store
        updateToolCall(tid, {
          snippet: output.substring(0, 200),
          done: true,
          duration: data.duration,
          is_error: data.is_error,
        });
        
        // Sprint 15: Add new assistant message placeholder for continuation
        // Each assistant response (including after tool calls) should be a separate block
        addMessage({
          role: 'assistant',
          content: '',
          _ts: Date.now() / 1000,
          _live: true,
        });
      });

      es.addEventListener('approval', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        // Handle approval request - show approval card
        data._session_id = sessionId;
        // Trigger approval UI
        window.dispatchEvent(new CustomEvent('approval-required', { detail: data }));
      });

      es.addEventListener('clarify', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        // Handle clarify request - show clarify card
        data._session_id = sessionId;
        window.dispatchEvent(new CustomEvent('clarify-required', { detail: data }));
      });

      es.addEventListener('title', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        updateCurrentSession({ title: data.title });
      });

      es.addEventListener('done', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        es.close();
        
        // Update session with final data
        updateCurrentSession({
          messages: data.session?.messages || messages,
          input_tokens: data.session?.input_tokens,
          output_tokens: data.session?.output_tokens,
          estimated_cost: data.session?.estimated_cost,
        });
        
        if (data.usage) {
          setLastUsage(data.usage);
        }
        
        setBusy(false);
        setAbortController(null);
        useChatStore.getState().setActiveStreamId(null);
        
        // Play notification sound
        window.dispatchEvent(new CustomEvent('chat-complete'));
        
        // Sprint 15: Reload session to get complete tool call outputs
        // The SSE stream may have incomplete tool result data
        loadSession(sessionId).then(() => {
          const session = useSessionStore.getState().currentSession;
          if (session?.messages) {
            setMessages(session.messages);
          }
          if (session?.tool_calls) {
            setToolCalls(session.tool_calls);
          }
        });
      });

      es.addEventListener('stream_end', () => {
        es.close();
      });

      es.addEventListener('compressed', (e: MessageEvent) => {
        // Show compression notice
        const data = JSON.parse(e.data);
        addMessage({
          role: 'assistant',
          content: `*[${data.message || 'Context compressed'}]*`,
          _ts: Date.now() / 1000,
        });
      });

      es.addEventListener('apperror', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        es.close();
        
        // Add error message
        const isRateLimit = data.type === 'rate_limit';
        const isNoResponse = data.type === 'no_response';
        let errorMsg = data.message;
        
        if (isRateLimit) {
          errorMsg = `**Rate limit reached:** ${data.message}`;
        } else if (isNoResponse) {
          errorMsg = '**No response received.** Check your API key and model selection.';
        } else {
          errorMsg = `**Error:** ${data.message}`;
          if (data.hint) {
            errorMsg += `\n\n*${data.hint}*`;
          }
        }
        
        // Update last assistant message with error
        appendToLastMessage('');
        addMessage({
          role: 'assistant',
          content: errorMsg,
          _ts: Date.now() / 1000,
        });
        
        setBusy(false);
        setAbortController(null);
      });

      es.addEventListener('warning', (e: MessageEvent) => {
        const data = JSON.parse(e.data);
        // Show warning toast
        window.dispatchEvent(new CustomEvent('show-toast', { 
          detail: { message: data.message, duration: 4000 } 
        }));
      });

      es.addEventListener('error', () => {
        // Handle error - attempt reconnect
        es.close();
        setBusy(false);
        
        // Add error message if not already added
        const lastMsg = messages[messages.length - 1];
        if (lastMsg?.role === 'assistant' && !lastMsg.content) {
          appendToLastMessage('');
          addMessage({
            role: 'assistant',
            content: '**Error:** Connection lost',
            _ts: Date.now() / 1000,
          });
        }
      });

      es.addEventListener('cancel', () => {
        es.close();
        setBusy(false);
        addMessage({
          role: 'assistant',
          content: '*Task cancelled.*',
          _ts: Date.now() / 1000,
        });
      });

    } catch (err) {
      setBusy(false);
      setAbortController(null);
      
      // Handle errors
      const errorMsg = (err as Error).message || 'Unknown error';
      addMessage({
        role: 'assistant',
        content: `**Error:** ${errorMsg}`,
        _ts: Date.now() / 1000,
      });
    }
  }, [addMessage, appendToLastMessage, appendReasoning, appendContentBlock, updateToolMessage, setBusy, setAbortController, setLastUsage, updateCurrentSession, messages]);

  const cancelStream = useCallback(async (streamId: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    
    try {
      await chatService.cancelStream(streamId);
    } catch {
      // Ignore cancel errors
    }
    
    setBusy(false);
    setAbortController(null);
  }, [setBusy, setAbortController]);

  return { startStream, cancelStream, streaming: useChatStore.getState().busy };
}
