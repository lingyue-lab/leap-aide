import React, { useRef, useCallback, useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '@/stores/useChatStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { modelsService, personalityService } from '@/services/api';
import ModeSelector from '@/components/common/ModeSelector';
import ApprovalIndicator from '@/components/common/ApprovalIndicator';
import ContextUsageIndicator from '@/components/chat/ContextUsageIndicator';
import { useModeShortcut } from '@/hooks/useModeShortcut';
import { useSlashCommands } from '@/hooks/useSlashCommands';
import SlashCommandDropdown from '@/components/chat/SlashCommandDropdown';
import type { SlashCommandItem } from '@/components/chat/SlashCommandDropdown';
import type { ModelGroup, Personality } from '@/types';

interface ChatComposerProps {
  onSend: (text: string, attachments?: string[], mode?: string) => void;
  onCancel: () => void;
  busy?: boolean;
  streaming?: boolean;
}

export default function ChatComposer({ onSend, onCancel, busy, streaming }: ChatComposerProps) {
  const { t } = useTranslation('chat');
  const { messages, pendingFiles, lastUsage } = useChatStore();
  const { settings } = useSettingsStore();
  const { currentSession, updateSessionModel, updateSessionReasoningEffort, updateSessionPersonality } = useSessionStore();
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

  // Eagerly load model list for /model arg completion
  useEffect(() => {
    modelsService.getModels()
      .then((resp) => setModelGroups(resp.groups ?? []))
      .catch(() => {});
  }, []);

  // Extract flat model ID list for slash-command dynamic arg completion
  const modelIds = useMemo(() => {
    const ids: string[] = [];
    for (const g of modelGroups) {
      for (const m of g.models) {
        ids.push(m.id);
      }
    }
    return ids;
  }, [modelGroups]);

  // Slash command autocomplete state
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashFilter, setSlashFilter] = useState('');
  const [slashSelectedIndex, setSlashSelectedIndex] = useState(0);
  const [cursorPos, setCursorPos] = useState(0);

  // Reasoning effort chip state
  const reasoningChipRef = useRef<HTMLButtonElement>(null);
  const reasoningPopoverRef = useRef<HTMLDivElement>(null);
  const [reasoningPickerOpen, setReasoningPickerOpen] = useState(false);

  const REASONING_EFFORT_LEVELS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'] as const;

  // Global reasoning config fallback (fetched on mount)
  const [globalReasoningEffort, setGlobalReasoningEffort] = useState<string>('medium');

  useEffect(() => {
    fetch('/api/reasoning', { credentials: 'include' })
      .then(r => r.json().catch(() => ({})))
      .then((d: any) => {
        if (d?.reasoning_effort) setGlobalReasoningEffort(d.reasoning_effort);
        if (typeof d?.show_reasoning === 'boolean') {
          useChatStore.getState().setShowReasoning(d.show_reasoning);
        }
      })
      .catch(() => {});
  }, []);

  // Resolve current reasoning effort: session override → global config → 'medium'
  const effectiveReasoningEffort: string =
    currentSession?.reasoning_effort || globalReasoningEffort || 'medium';

  // Personality chip state
  const personalityChipRef = useRef<HTMLButtonElement>(null);
  const personalityPopoverRef = useRef<HTMLDivElement>(null);
  const [personalityPickerOpen, setPersonalityPickerOpen] = useState(false);
  const [personalities, setPersonalities] = useState<Personality[]>([]);

  const effectivePersonality = currentSession?.personality || null;

  // Workspace chip state
  const [workspacePickerOpen, setWorkspacePickerOpen] = useState(false);
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const assignWorkspace = useSessionStore((s) => s.assignWorkspace);
  const currentWorkspacePath: string | undefined = (currentSession as any)?.workspace;
  const effectiveWorkspace = workspaces.find((w: any) => w.path === currentWorkspacePath) ?? null;
  const effectiveWorkspaceName: string | null = effectiveWorkspace?.name ?? null;

  // Close workspace popover on outside click
  useEffect(() => {
    if (!workspacePickerOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      const popoverEl = document.querySelector('.workspace-popover');
      const chipEl = document.querySelector('.icon-btn.workspace');
      if (
        popoverEl && !popoverEl.contains(e.target as Node) &&
        chipEl && !chipEl.contains(e.target as Node)
      ) {
        setWorkspacePickerOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [workspacePickerOpen]);

  const handleSelectWorkspace = useCallback(async (workspacePath: string | null) => {
    if (!currentSession) return;
    try {
      await assignWorkspace(currentSession.session_id, workspacePath || '');
    } catch (err) {
      console.error('Failed to assign workspace:', err);
    }
    setWorkspacePickerOpen(false);
  }, [currentSession, assignWorkspace]);

  const workspaceDisabled = !currentSession || (currentSession as any)?._readonly || compressing || (currentSession as any)?.is_cli_session;

  // Fetch available personalities on mount
  useEffect(() => {
    personalityService.getAll()
      .then(setPersonalities)
      .catch(() => {});
  }, []);

  // Close personality popover on outside click
  useEffect(() => {
    if (!personalityPickerOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        personalityPopoverRef.current && !personalityPopoverRef.current.contains(e.target as Node) &&
        personalityChipRef.current && !personalityChipRef.current.contains(e.target as Node)
      ) {
        setPersonalityPickerOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [personalityPickerOpen]);

  const handlePersonalitySelect = useCallback(async (name: string | null) => {
    setPersonalityPickerOpen(false);
    if (!currentSession?.session_id) return;
    try {
      await updateSessionPersonality(currentSession.session_id, name ?? '');
    } catch {
      // silently ignore
    }
  }, [currentSession, updateSessionPersonality]);

  // Voice input state — Web Speech API
  const { i18n } = useTranslation();
  const [voiceSupported, setVoiceSupported] = useState(false);
  const [voiceListening, setVoiceListening] = useState(false);
  const recognitionRef = useRef<any>(null);

  useEffect(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    setVoiceSupported(!!SR);
  }, []);

  const handleVoiceToggle = useCallback(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return;

    if (voiceListening && recognitionRef.current) {
      recognitionRef.current.stop();
      return;
    }

    const recognition = new SR();
    // Use browser/system locale for speech recognition, not UI language.
    // navigator.language reflects the OS language which maps to the user's
    // spoken language. Fall back to i18n language, then en-US.
    const speechLang = (navigator.language || i18n.language || 'en-US').toLowerCase();
    recognition.lang = speechLang.startsWith('zh') ? 'zh-CN'
      : speechLang.startsWith('ja') ? 'ja-JP'
      : speechLang.startsWith('ko') ? 'ko-KR'
      : 'en-US';
    recognition.interimResults = false;
    recognition.continuous = false;

    recognition.onresult = (event: any) => {
      const transcript: string = event.results[0][0].transcript;
      const textarea = textareaRef.current;
      if (textarea) {
        const pos = textarea.selectionStart ?? textarea.value.length;
        const before = textarea.value.slice(0, pos);
        const after = textarea.value.slice(pos);
        const newValue = before + transcript + ' ' + after;
        setInput(newValue);
        const newPos = pos + transcript.length + 1;
        setTimeout(() => {
          textarea.setSelectionRange(newPos, newPos);
          textarea.focus();
        }, 0);
      }
      setVoiceListening(false);
    };

    recognition.onerror = () => setVoiceListening(false);
    recognition.onend = () => setVoiceListening(false);

    recognitionRef.current = recognition;
    recognition.start();
    setVoiceListening(true);
  }, [voiceListening, i18n.language]);

  // Slash command autocomplete — dynamic args including personality names
  const personalityNames = useMemo(() => ['none', ...personalities.map(p => p.name)], [personalities]);

  const slashDynamicArgs = useMemo(() => ({
    model: modelIds,
    reasoning: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'show', 'hide', 'default'],
    personality: personalityNames,
  }), [modelIds, personalityNames]);

  const { matchCommands, flattenMatching } = useSlashCommands(slashDynamicArgs);

  const matchingGroups = useMemo(() => matchCommands(slashFilter), [matchCommands, slashFilter]);
  const flatCommandCount = useMemo(() => flattenMatching(matchingGroups).length, [matchingGroups, flattenMatching]);

  // Determine if current model supports extended thinking
  const currentModelSupportsThinking = useMemo(() => {
    const sessionModelId = currentSession?.model ?? '';
    if (!sessionModelId || modelGroups.length === 0) return true; // default: enabled until models load
    for (const g of modelGroups) {
      for (const m of g.models) {
        if (m.id === sessionModelId) {
          return m.supportsExtendedThinking !== false;
        }
      }
    }
    return true; // unknown model: fail open
  }, [currentSession?.model, modelGroups]);

  // Close reasoning popover on outside click
  useEffect(() => {
    if (!reasoningPickerOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        reasoningPopoverRef.current && !reasoningPopoverRef.current.contains(e.target as Node) &&
        reasoningChipRef.current && !reasoningChipRef.current.contains(e.target as Node)
      ) {
        setReasoningPickerOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [reasoningPickerOpen]);

  const handleReasoningSelect = useCallback(async (effort: string) => {
    setReasoningPickerOpen(false);
    if (!currentSession?.session_id) return;
    try {
      await updateSessionReasoningEffort(currentSession.session_id, effort);
    } catch {
      // silently ignore
    }
  }, [currentSession, updateSessionReasoningEffort]);

  const handleCompressingChange = useCallback((v: boolean) => {
    setCompressing(v);
  }, []);

  const sendByCtrlEnter = settings?.send_key === 'ctrl+enter';
  const hasInput = input.trim().length > 0 || pendingFiles.length > 0;
  const sendMode = busy ? (settings?.busy_input_mode ?? 'interrupt') : undefined;
  // Allow sending while busy if mode is interrupt, queue, or steer
  const busyAllowsSend = !!(busy && sendMode && (sendMode === 'interrupt' || sendMode === 'queue' || sendMode === 'steer'));
  const canSend = hasInput && !compressing && (!busy || busyAllowsSend);

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
    
    const mode = busy ? (settings?.busy_input_mode ?? 'interrupt') : undefined;
    onSend(text, pendingFiles.length > 0 ? pendingFiles.map(f => f.name) : undefined, mode);
  }, [canSend, input, pendingFiles, onSend, busy, settings?.busy_input_mode]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Check for IME composition
    if (e.nativeEvent.isComposing) return;

    // ── Slash command dropdown keyboard handling ──
    if (slashOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSlashSelectedIndex((i) => Math.min(i + 1, flatCommandCount - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSlashSelectedIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        const flat = flattenMatching(matchingGroups);
        if (flat.length > 0) {
          const idx = Math.min(slashSelectedIndex, flat.length - 1);
          const cmd = flat[idx];
          const displayName = cmd.displayName || cmd.name;

          const lastSlashPos = input.lastIndexOf('/');
          const afterSlash = input.slice(lastSlashPos + 1);

          // Enter when the user has already typed what completion would
          // produce → close dropdown and submit as-is (don't overwrite).
          // For example: /memory approve a1b2c3d4 should submit, not
          // replace with just /memory.  But /model clau should still
          // complete to /model claude-opus-4-8.
          const completionTarget = displayName.split('|')[0].trim();
          if (
            e.key === 'Enter' &&
            (afterSlash === completionTarget || afterSlash.startsWith(completionTarget + ' '))
          ) {
            setSlashOpen(false);
            setSlashFilter('');
            handleSubmit();
            return;
          }

          // Replace /partial with /command-name
          const beforeSlash = input.slice(0, input.lastIndexOf('/'));
          const afterCursor = input.slice(cursorPos);
          const newValue = beforeSlash + '/' + displayName + ' ' + afterCursor;
          setInput(newValue);
          setSlashOpen(false);
          setSlashFilter('');
          const newCursorPos = beforeSlash.length + displayName.length + 2; // +2 for / and space
          setTimeout(() => {
            textareaRef.current?.setSelectionRange(newCursorPos, newCursorPos);
            textareaRef.current?.focus();
          }, 0);
        } else {
          // No matching commands — close dropdown.
          // On Enter, also submit the current input normally.
          setSlashOpen(false);
          setSlashFilter('');
          if (e.key === 'Enter') {
            handleSubmit();
          }
        }
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setSlashOpen(false);
        return;
      }
    }

    const isEnter = e.key === 'Enter';
    const shouldSend = sendByCtrlEnter ? (isEnter && (e.ctrlKey || e.metaKey)) : (isEnter && !e.shiftKey);

    if (shouldSend) {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === 'Escape' && busy) {
      e.preventDefault();
      onCancel();
    }
  }, [sendByCtrlEnter, handleSubmit, busy, onCancel, slashOpen, input, cursorPos, matchingGroups, flatCommandCount, flattenMatching, slashSelectedIndex]);

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
                        alignItems: 'flex-start',
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
                      <span style={{
                        display: 'flex',
                        flexDirection: 'column',
                        overflow: 'hidden',
                        minWidth: 0,
                      }}>
                        <span style={{
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          fontSize: 12,
                          lineHeight: 1.4,
                        }}>
                          {m.label}
                        </span>
                        <span style={{
                          fontSize: 10,
                          color: 'var(--muted)',
                          fontFamily: 'monospace',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          lineHeight: 1.3,
                        }}>
                          {m.id}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      )}

      {/* Reasoning effort popover */}
      {reasoningPickerOpen && (
        <div
          ref={reasoningPopoverRef}
          style={{
            position: 'absolute',
            bottom: 'calc(100% - 12px)',
            left: 200,
            minWidth: 160,
            maxWidth: 200,
            maxHeight: 300,
            overflowY: 'auto',
            background: 'var(--surface)',
            border: '1px solid var(--border2)',
            borderRadius: 10,
            boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            zIndex: 200,
            padding: 0,
          }}
        >
          {/* Popover header */}
          <div style={{
            padding: '8px 14px 6px',
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            color: 'var(--muted)',
            borderBottom: '1px solid var(--border2)',
          }}>
            {t('reasoning.label')}
          </div>
          <div style={{ padding: '6px 0' }}>
          {REASONING_EFFORT_LEVELS.map((level) => {
            const isActive = effectiveReasoningEffort === level;
            return (
              <button
                key={level}
                onClick={() => handleReasoningSelect(level)}
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
                <span>{t(`reasoning.${level}`)}</span>
              </button>
            );
          })}
          </div>
        </div>
      )}

      {/* Personality popover */}
      {personalityPickerOpen && (
        <div
          ref={personalityPopoverRef}
          style={{
            position: 'absolute',
            bottom: 'calc(100% - 12px)',
            left: 280,
            minWidth: 200,
            maxWidth: 280,
            maxHeight: 280,
            overflowY: 'auto',
            background: 'var(--surface)',
            border: '1px solid var(--border2)',
            borderRadius: 10,
            boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            zIndex: 200,
            padding: 0,
          }}
        >
          <div style={{
            padding: '8px 14px 6px',
            fontSize: 10,
            fontWeight: 600,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            color: 'var(--muted)',
            borderBottom: '1px solid var(--border2)',
          }}>
            Personality
          </div>
          <div style={{ padding: '6px 0' }}>
            {/* None option */}
            <button
              onClick={() => handlePersonalitySelect(null)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                width: '100%',
                padding: '7px 14px',
                background: !effectivePersonality ? 'color-mix(in srgb, var(--blue) 15%, transparent)' : 'transparent',
                border: 'none',
                cursor: 'pointer',
                fontSize: 12,
                color: !effectivePersonality ? 'var(--blue)' : 'var(--text)',
                textAlign: 'left',
              }}
            >
              {!effectivePersonality && (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{ flexShrink: 0 }}>
                  <path d="M2 6L5 9L10 3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              )}
              {effectivePersonality && <span style={{ width: 12, flexShrink: 0 }} />}
              <span>None</span>
            </button>
            {personalities.map((p) => {
              const isActive = effectivePersonality === p.name;
              return (
                <button
                  key={p.name}
                  onClick={() => handlePersonalitySelect(p.name)}
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
                  <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
                    <span style={{ fontSize: 12, lineHeight: 1.4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {p.name}
                    </span>
                    {p.description && (
                      <span style={{ fontSize: 10, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', lineHeight: 1.3 }}>
                        {p.description}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Workspace popover */}
      {workspacePickerOpen && (
        <div className="workspace-popover">
          <div className="workspace-popover-header">Workspace</div>
          <div className="workspace-popover-list">
            {/* None option */}
            <button
              className={`workspace-popover-item${!currentWorkspacePath ? ' selected' : ''}`}
              onClick={() => handleSelectWorkspace(null)}
            >
              <span className="ws-name">None</span>
              {!currentWorkspacePath && (
                <svg className="ws-check" width="14" height="14" viewBox="0 0 24 24"
                     fill="none" stroke="currentColor" strokeWidth="2.5">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
              )}
            </button>
            {workspaces.length === 0 ? (
              <div className="workspace-popover-empty">No workspaces added</div>
            ) : (
              workspaces.map((ws: any) => (
                <button
                  key={ws.path}
                  className={`workspace-popover-item${currentWorkspacePath === ws.path ? ' selected' : ''}`}
                  onClick={() => handleSelectWorkspace(ws.path)}
                >
                  <svg className="ws-icon" width="14" height="14" viewBox="0 0 24 24"
                       fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                  </svg>
                  <span className="ws-name">{ws.name}</span>
                  {ws.readonly && (
                    <span style={{fontSize: 10, opacity: 0.5, flexShrink: 0}}>RO</span>
                  )}
                  {currentWorkspacePath === ws.path && (
                    <svg className="ws-check" width="14" height="14" viewBox="0 0 24 24"
                         fill="none" stroke="currentColor" strokeWidth="2.5">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                  )}
                </button>
              ))
            )}
          </div>
        </div>
      )}

      {/* Slash command autocomplete dropdown */}
      {slashOpen && (
        <div style={{ position: 'relative' }}>
          <SlashCommandDropdown
            groups={matchingGroups}
            selectedIndex={slashSelectedIndex}
            onSelect={(cmd: SlashCommandItem) => {
              let displayName = cmd.displayName || cmd.name;
              // If there are multiple args (pipe-separated), use only the first
              const pipeIdx = displayName.indexOf('|');
              if (pipeIdx > 0) displayName = displayName.slice(0, pipeIdx);
              const beforeSlash = input.slice(0, input.lastIndexOf('/'));
              const afterCursor = input.slice(cursorPos);
              const newValue = beforeSlash + '/' + displayName + ' ' + afterCursor;
              setInput(newValue);
              setSlashOpen(false);
              setSlashFilter('');
              const newCursorPos = beforeSlash.length + displayName.length + 2;
              setTimeout(() => {
                textareaRef.current?.setSelectionRange(newCursorPos, newCursorPos);
                textareaRef.current?.focus();
              }, 0);
            }}
            onMouseEnterIndex={setSlashSelectedIndex}
          />
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
          onChange={(e) => {
            const value = e.target.value;
            const pos = e.target.selectionStart ?? value.length;
            setInput(value);
            setCursorPos(pos);

            // Detect slash at word boundary: start of input or preceded by whitespace
            const beforeCursor = value.slice(0, pos);
            const lastSlashIdx = (() => {
              let idx = beforeCursor.lastIndexOf('/');
              if (idx === -1) return -1;
              if (idx === 0 || /\s/.test(beforeCursor[idx - 1])) return idx;
              return -1;
            })();

            if (lastSlashIdx >= 0) {
              const partial = beforeCursor.slice(lastSlashIdx + 1);
              // Show dropdown for slash in command position;
              // matchCommands handles both command-name and arg-level filtering.
              // Only open if there are actual matches to avoid blocking
              // normal typing after a completed /command.
              const matched = matchCommands(partial);
              if (matched.length > 0) {
                setSlashOpen(true);
                setSlashFilter(partial);
                setSlashSelectedIndex(0);
              } else {
                setSlashOpen(false);
                setSlashFilter('');
              }
            } else {
              setSlashOpen(false);
              setSlashFilter('');
            }
          }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          disabled={compressing || (busy && !streaming)}
        />

        {/* Footer */}
        <div className="composer-footer">
          <div className="composer-left">
            {/* Voice input button — Web Speech API */}
            {voiceSupported && (
              <button
                className={`icon-btn${voiceListening ? ' voice-active' : ''}`}
                onClick={handleVoiceToggle}
                title={voiceListening ? t('composer.voiceStop') : t('composer.voiceStart')}
                disabled={compressing || (busy && !streaming)}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill={voiceListening ? 'var(--red)' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                  <line x1="12" y1="19" x2="12" y2="23"/>
                  <line x1="8" y1="23" x2="16" y2="23"/>
                </svg>
              </button>
            )}
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
            
            {/* Model — icon button (was chip) */}
            <button
              ref={modelChipRef}
              className="icon-btn model"
              onClick={() => setModelPickerOpen((v) => !v)}
              title={`Model: ${displayModel}`}
              disabled={compressing}
              style={{ cursor: compressing ? 'not-allowed' : 'pointer' }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="2" y="3" width="20" height="14" rx="2"/>
                <path d="M8 21h8M12 17v4"/>
              </svg>
            </button>

            {/* Reasoning effort — icon button */}
            <button
              ref={reasoningChipRef}
              className={`icon-btn reasoning${!currentModelSupportsThinking ? ' inactive' : ''}`}
              onClick={() => currentModelSupportsThinking && setReasoningPickerOpen((v) => !v)}
              title={
                !currentModelSupportsThinking
                  ? t('reasoning.unsupported')
                  : `${t('reasoning.label')}: ${t(`reasoning.${effectiveReasoningEffort}`)}`
              }
              disabled={compressing || !currentModelSupportsThinking}
              style={{ cursor: compressing || !currentModelSupportsThinking ? 'not-allowed' : 'pointer' }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3"/>
                <path d="M12 1v4M12 19v4M4.2 4.2l2.8 2.8M17 17l2.8 2.8M1 12h4M19 12h4M4.2 19.8l2.8-2.8M17 7l2.8-2.8"/>
              </svg>
            </button>

            {/* Personality — icon button */}
            <button
              ref={personalityChipRef}
              className="icon-btn personality"
              onClick={() => setPersonalityPickerOpen((v) => !v)}
              title={effectivePersonality ? `Personality: ${effectivePersonality}` : 'Personality (none)'}
              disabled={compressing}
              style={{ cursor: compressing ? 'not-allowed' : 'pointer' }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="8" r="4"/>
                <path d="M6 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2"/>
              </svg>
            </button>

            {/* Workspace — icon button */}
            {!(currentSession as any)?.is_cli_session && (
              <button
                className={`icon-btn workspace${workspaceDisabled ? ' inactive' : ''}`}
                onClick={() => !workspaceDisabled && setWorkspacePickerOpen(!workspacePickerOpen)}
                title={effectiveWorkspaceName || 'Workspace'}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                </svg>
              </button>
            )}

            {/* Skills — icon button, opens skills drawer */}
            <button
              className="icon-btn skills"
              onClick={() => {
                window.dispatchEvent(new CustomEvent('hermes:toggle-skills-panel'));
              }}
              title={(() => {
                const skills = (currentSession as any)?.skills;
                if (!skills || skills.length === 0) return t('session_skills.none', { ns: 'common' });
                return t('session_skills.count', { ns: 'common', count: skills.length });
              })()}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20.24 12.24a6 6 0 0 0-8.49-8.49L5 10.5V19h8.5z"/>
                <line x1="16" y1="8" x2="2" y2="22"/>
                <line x1="17.5" y1="15" x2="9" y2="15"/>
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
