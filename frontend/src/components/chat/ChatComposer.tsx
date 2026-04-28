import React, { useRef, useCallback, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '@/stores/useChatStore';
import { useSettingsStore } from '@/stores/useSettingsStore';

interface ChatComposerProps {
  onSend: (text: string, attachments?: string[]) => void;
  onCancel: () => void;
  busy?: boolean;
  streaming?: boolean;
}

export default function ChatComposer({ onSend, onCancel, busy, streaming }: ChatComposerProps) {
  const { t } = useTranslation('chat');
  const { pendingFiles } = useChatStore();
  const { settings } = useSettingsStore();
  
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [input, setInput] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  
  const sendByCtrlEnter = settings?.send_key === 'ctrl+enter';
  const hasInput = input.trim().length > 0 || pendingFiles.length > 0;
  const canSend = hasInput && !busy;

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
  }, [input]);

  const handleSubmit = useCallback(() => {
    if (!canSend) return;
    const text = input.trim();
    if (!text && pendingFiles.length === 0) return;
    
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    
    onSend(text);
  }, [canSend, input, pendingFiles, onSend]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Check for IME composition
    if (e.nativeEvent.isComposing) return;
    
    const isEnter = e.key === 'Enter';
    const shouldSend = sendByCtrlEnter ? (isEnter && (e.ctrlKey || e.metaKey)) : (isEnter && !e.shiftKey);
    
    if (shouldSend) {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === 'Escape' && busy) {
      e.preventDefault();
      onCancel();
    }
  }, [sendByCtrlEnter, handleSubmit, busy, onCancel]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    files.forEach((file) => {
      useChatStore.getState().addPendingFile(file);
    });
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setIsDragOver(false);
  }, []);

  const handlePaste = useCallback(async (e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData.items);
    for (const item of items) {
      if (item.kind === 'file') {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) {
          useChatStore.getState().addPendingFile(file);
        }
      }
    }
  }, []);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    files.forEach((file) => {
      useChatStore.getState().addPendingFile(file);
    });
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, []);

  const handleAttachClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleRemoveFile = useCallback((index: number) => {
    useChatStore.getState().removePendingFile(index);
  }, []);

  return (
    <div 
      className={`composer-wrap ${isDragOver ? 'drag-over' : ''}`}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
    >
      <div className="composer-box">
        {/* Drop hint - only visible when dragging */}
        {isDragOver && (
          <div className="drop-hint">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            {t('composer.drop_files')}
          </div>
        )}
        
        {/* Attach tray */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept="image/*,text/*,application/pdf,.md,.py,.js,.ts,.yaml,.yml,.toml,.csv,.sh,.txt,.log,.env"
          style={{ display: 'none' }}
          onChange={handleFileSelect}
        />
        
        <div className={`attach-tray ${pendingFiles.length > 0 ? 'has-files' : ''}`}>
          {pendingFiles.map((file, index) => (
            <div key={index} className="attach-chip">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
              <span>{file.name}</span>
              <button onClick={() => handleRemoveFile(index)}>×</button>
            </div>
          ))}
        </div>
        
        {/* Textarea */}
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          rows={1}
          placeholder={t('composer.placeholder')}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          disabled={busy && !streaming}
        />
        
        {/* Footer */}
        <div className="composer-footer">
          <div className="composer-left">
            <button
              className="icon-btn"
              onClick={handleAttachClick}
              title={t('composer.attach')}
              disabled={busy && !streaming}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            
            <div className="composer-divider" />
            
            {/* Model chip placeholder */}
            <div className="chip model">{settings?.model?.split('/').pop() || 'Model'}</div>
          </div>
          
          <div className="composer-right">
            {busy && streaming && (
              <button
                className="cancel-btn"
                onClick={onCancel}
                title={t('composer.stop')}
                aria-label={t('composer.stop')}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="5" y="5" width="14" height="14" rx="2" />
                </svg>
              </button>
            )}
            
            <button
              className="send-btn"
              onClick={handleSubmit}
              title={t('composer.send')}
              disabled={!canSend}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
