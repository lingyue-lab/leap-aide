import React, { useRef, useCallback, useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '@/stores/useChatStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useSessionStore } from '@/stores/useSessionStore';
import { modelsService } from '@/services/api';
import ModeSelector from '@/components/common/ModeSelector';
import ApprovalIndicator from '@/components/common/ApprovalIndicator';
import ContextUsageIndicator from '@/components/chat/ContextUsageIndicator';
import { useModeShortcut } from '@/hooks/useModeShortcut';
import type { ModelGroup } from '@/types';

interface ChatComposerProps {
  onSend: (text: string, attachments?: string[]) => void;
  onCancel: () => void;
  busy?: boolean;
  streaming?: boolean;
}

export default function ChatComposer({ onSend, onCancel, busy, streaming }: ChatComposerProps) {
  const { t } = useTranslation('chat');
  const { messages, pendingFiles, lastUsage } = useChatStore();
  const { settings } = useSettingsStore();
  const { currentSession, updateSessionModel } = useSessionStore();
  useModeShortcut();

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const modelChipRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const [input, setInput] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [modelGroups, setModelGroups] = useState<ModelGroup[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [compressing, setCompressing] = useState(false);

  const handleCompressingChange = useCallback((v: boolean) => {
    setCompressing(v);
  }, []);

  const sendByCtrlEnter = settings?.send_key === 'ctrl+enter';
  const hasInput = input.trim().length > 0 || pendingFiles.length > 0;
  const canSend = hasInput && !busy && !compressing;

  // Derive last compression time: prefer persisted session field (survives page
  // refresh), fall back to scanning live messages' _compression metadata (SSE).
  const lastCompressionTime = useMemo(() => {
    const fromSession = currentSession?.last_compression_time;
    if (fromSession && fromSession > 0) return fromSession;
    let latest = 0;
    for (const msg of messages) {
      if (msg._compression && msg._compression.compression_time > latest) {
        latest = msg._compression.compression_time;
      }
    }
    return latest > 0 ? latest : null;
  }, [currentSession?.last_compression_time, messages]);

  // Resolve displayed model: session model → settings default → 'Model'
  const sessionModel = currentSession?.model ?? '';
  const effectiveModel = sessionModel || settings?.model || '';
  const displayModel = effectiveModel
    ? effectiveModel.split('/').pop() ?? effectiveModel
    : 'Model';

  // Load models when picker opens
  useEffect(() => {
    if (!modelPickerOpen) return;
    setModelsLoading(true);
    modelsService.getModels()
      .then((resp) => setModelGroups(resp.groups ?? []))
      .catch(() => setModelGroups([]))
      .finally(() => setModelsLoading(false));
  }, [modelPickerOpen]);

  // Close popover on outside click
  useEffect(() => {
    if (!modelPickerOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        popoverRef.current && !popoverRef.current.contains(e.target as Node) &&
        modelChipRef.current && !modelChipRef.current.contains(e.target as Node)
      ) {
        setModelPickerOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [modelPickerOpen]);

  const handleModelSelect = useCallback(async (modelId: string) => {
    setModelPickerOpen(false);
    if (!currentSession?.session_id) return;
    try {
      await updateSessionModel(currentSession.session_id, modelId);
    } catch {
      // silently ignore
    }
  }, [currentSession, updateSessionModel]);

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
      className={`composer-wrap ${isDragOver ? 'drag-over' : ''}${compressing ? ' compressing' : ''}`}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
    >
      {/* Model picker popover — at composer-wrap level to avoid any overflow clipping */}
      {modelPickerOpen && (
        <div
          ref={popoverRef}
          style={{
            position: 'absolute',
            bottom: 'calc(100% - 12px)',
            left: 48,
            minWidth: 260,
            maxWidth: 320,
            maxHeight: 320,
            overflowY: 'auto',
            background: 'var(--surface)',
            border: '1px solid var(--border2)',
            borderRadius: 10,
            boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            zIndex: 200,
            padding: '6px 0',
          }}
        >
          {modelsLoading ? (
            <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--muted)' }}>
              Loading…
            </div>
          ) : modelGroups.length === 0 ? (
            <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--muted)' }}>
              No models available
            </div>
          ) : (
            modelGroups.map((group) => (
              <div key={group.provider}>
                <div style={{
                  padding: '4px 12px 2px',
                  fontSize: 10,
                  fontWeight: 600,
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  color: 'var(--muted)',
                }}>
                  {group.provider}
                </div>
                {group.models.map((m) => {
                  const isActive = effectiveModel === m.id;
                  return (
                    <button
                      key={m.id}
                      onClick={() => handleModelSelect(m.id)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        width: '100%',
                        padding: '7px 14px',
                        background: isActive ? 'color-mix(in srgb, var(--blue) 15%, transparent)' : 'transparent',
                        border: 'none',
                        cursor: 'pointer',
                        fontSize: 12,
                        color: isActive ? 'var(--blue)' : 'var(--text)',
                        textAlign: 'left',
                      }}
                    >
                      {isActive && (
                        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{ flexShrink: 0 }}>
                          <path d="M2 6L5 9L10 3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                        </svg>
                      )}
                      {!isActive && <span style={{ width: 12, flexShrink: 0 }} />}
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {m.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      )}

      <div className="composer-box">
        {/* Compression overlay — blocks interaction and shows status */}
        {compressing && (
          <div className="composer-compress-overlay">
            <span className="composer-compress-overlay-text">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ animation: 'spin 1s linear infinite' }}>
                <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
                <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
              </svg>
              {t('sessions.compressing', { ns: 'common' })}
            </span>
          </div>
        )}
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
          disabled={compressing || (busy && !streaming)}
        />

        {/* Footer */}
        <div className="composer-footer">
          <div className="composer-left">
            <button
              className="icon-btn"
              onClick={handleAttachClick}
              title={t('composer.attach')}
              disabled={compressing || (busy && !streaming)}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            
            <div className="composer-divider" />
            
            {/* Model chip */}
            <button
              ref={modelChipRef}
              className="chip model"
              onClick={() => setModelPickerOpen((v) => !v)}
              title="Switch model"
              disabled={compressing}
              style={{ cursor: compressing ? 'not-allowed' : 'pointer' }}
            >
              {displayModel}
              <svg width="8" height="5" viewBox="0 0 8 5" fill="none" style={{ marginLeft: 4, opacity: 0.6 }}>
                <path d="M1 4L4 1L7 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>

          {/* Mode selector — between model chip and send button */}
          <ModeSelector sessionId={currentSession?.session_id ?? null} />

          {/* Session approval indicator — shows approved patterns count + revoke popover */}
          <ApprovalIndicator />

          <div className="composer-right">
            <ContextUsageIndicator usage={lastUsage} session={currentSession} lastCompressionTime={lastCompressionTime} streaming={streaming} onCompressingChange={handleCompressingChange} />
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
