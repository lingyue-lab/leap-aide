import React, { useRef, useCallback, useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '@/stores/useChatStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useSessionStore } from '@/stores/useSessionStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useSkillStore } from '@/stores/useSkillStore';
import { modelsService, personalityService } from '@/services/api';
import ModeSelector from '@/components/common/ModeSelector';
import ApprovalIndicator from '@/components/common/ApprovalIndicator';
import SingleSelection from '@/components/common/SingleSelection';
import MultiSelection from '@/components/common/MultiSelection';
import ContextUsageIndicator from '@/components/chat/ContextUsageIndicator';
import { useModeShortcut } from '@/hooks/useModeShortcut';
import { useSlashCommands } from '@/hooks/useSlashCommands';
import { markSessionSeen } from '@/utils/sessionSeen';
import SlashCommandDropdown from '@/components/chat/SlashCommandDropdown';
import { useIsBusy } from '@/utils/sessionBusy';
import { useWorkspaceDisplay } from '@/hooks/useWorkspaceDisplay';
import type { SlashCommandItem } from '@/components/chat/SlashCommandDropdown';
import type { ModelGroup, Personality } from '@/types';
import { Icon } from '@/components/common/Icons';

interface ChatComposerProps {
  onSend: (text: string, attachments?: string[], mode?: string) => void;
  onCancel: () => void;
  cardVisible?: boolean;
}

export default function ChatComposer({ onSend, onCancel, cardVisible: cardVisibleProp }: ChatComposerProps) {
  const busy = useIsBusy();
  const { t } = useTranslation('chat');
  const { messages, pendingFiles, lastUsage } = useChatStore();
  const { settings } = useSettingsStore();
  const { currentSession, updateSessionModel, updateSessionReasoningEffort, updateSessionPersonality } = useSessionStore();
  useModeShortcut();

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [input, setInput] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);
  const [modelGroups, setModelGroups] = useState<ModelGroup[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const cardVisible = cardVisibleProp ?? false;

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

  // Reasoning effort options
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

  // Personality state
  const [personalities, setPersonalities] = useState<Personality[]>([]);
  const effectivePersonality = currentSession?.personality || null;

  // Workspace chip state
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const assignWorkspace = useSessionStore((s) => s.assignWorkspace);
  const currentWorkspacePath: string | undefined = (currentSession as any)?.workspace;
  const { label: effectiveWorkspaceName } = useWorkspaceDisplay(currentWorkspacePath);
  // WebUI sessions cannot select external workspaces
  const isWebUISession = !(currentSession as any)?._readonly && !(currentSession as any)?.is_cli_session;

  const handleSelectWorkspace = useCallback(async (workspacePath: string | null) => {
    if (!currentSession) return;
    try {
      await assignWorkspace(currentSession.session_id, workspacePath || '');
    } catch (err) {
      console.error('Failed to assign workspace:', err);
    }
  }, [currentSession, assignWorkspace]);

  const workspaceDisabled = !currentSession || (currentSession as any)?._readonly || (currentSession as any)?.is_cli_session;

  // Fetch available personalities on mount
  useEffect(() => {
    personalityService.getAll()
      .then(setPersonalities)
      .catch(() => {});
  }, []);

  const handlePersonalitySelect = useCallback(async (name: string | null) => {
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

  const handleReasoningSelect = useCallback(async (effort: string | null) => {
    if (!currentSession?.session_id || !effort) return;
    try {
      await updateSessionReasoningEffort(currentSession.session_id, effort);
    } catch {
      // silently ignore
    }
  }, [currentSession, updateSessionReasoningEffort]);

  // ── Skills multi-selection ─────────────────────────────────────────────
  const skills = useSkillStore((s) => s.skills);
  const loadSkills = useSkillStore((s) => s.loadSkills);
  const updateSessionSkills = useSessionStore((s) => s.updateSessionSkills);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const sessions = useSessionStore((s) => s.sessions);

  const activeItem = sessions.find((s) => s.session_id === currentSessionId);
  const sessionSkills: string[] | null =
    activeItem !== undefined
      ? (activeItem.skills ?? null)
      : (currentSession?.skills ?? null);
  const activeSessionId = currentSessionId ?? currentSession?.session_id;

  const isDefault = sessionSkills === null;

  const selectedSkillValues: string[] = isDefault
    ? skills.map(s => s.dir_name || s.name)
    : (sessionSkills ?? []);

  const skillOptionGroups = useMemo(() => {
    const groups: Record<string, { value: string; label: string; description?: string }[]> = {};
    for (const skill of skills) {
      const cat = skill.category || 'General';
      if (!groups[cat]) groups[cat] = [];
      groups[cat].push({
        value: skill.dir_name || skill.name,
        label: skill.label || skill.name,
        description: skill.description,
      });
    }
    return Object.keys(groups).sort().map(header => ({
      header,
      items: groups[header],
    }));
  }, [skills]);

  const handleSkillsChange = useCallback(async (selected: string[]) => {
    if (!activeSessionId) return;
    try {
      await updateSessionSkills(activeSessionId, selected);
    } catch {
      // silently ignore
    }
  }, [activeSessionId, updateSessionSkills]);

  const handleSkillsOpen = useCallback(() => {
    if (skills.length === 0) loadSkills();
  }, [skills.length, loadSkills]);

  const skillsTriggerLabel = (() => {
    if (!skills.length) return t('session_skills.panel_title', { ns: 'common' });
    return t('session_skills.count', { ns: 'common', count: selectedSkillValues.length, total: skills.length });
  })();

  const sendByCtrlEnter = settings?.send_key === 'ctrl+enter';
  const hasInput = input.trim().length > 0 || pendingFiles.length > 0;
  const sendMode = busy ? (settings?.busy_input_mode ?? 'interrupt') : undefined;
  // Allow sending while busy if mode is interrupt, queue, or steer
  const busyAllowsSend = !!(busy && sendMode && (sendMode === 'interrupt' || sendMode === 'queue' || sendMode === 'steer'));
  const canSend = hasInput && (!busy || busyAllowsSend);

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

  // Load models data
  const [modelDataLoaded, setModelDataLoaded] = useState(false);
  const loadModels = useCallback(() => {
    if (modelDataLoaded) return;
    setModelsLoading(true);
    modelsService.getModels()
      .then((resp) => setModelGroups(resp.groups ?? []))
      .catch(() => setModelGroups([]))
      .finally(() => { setModelsLoading(false); setModelDataLoaded(true); });
  }, [modelDataLoaded]);

  const handleModelSelect = useCallback(async (modelId: string | null) => {
    if (!modelId) return;
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
    const h = Math.min(textarea.scrollHeight, 200);
    textarea.style.height = h + 'px';
    textarea.style.overflowY = h >= 200 ? 'auto' : 'hidden';
  }, [input]);

  const handleSubmit = useCallback(() => {
    if (!canSend) return;
    const text = input.trim();
    if (!text && pendingFiles.length === 0) return;
    
    setInput('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    
    // Slash commands (/help, /status, /model, etc.) are read-only queries
    // that don't affect the LLM context — never interrupt a running stream.
    const isSlash = text.startsWith('/');
    const mode = (busy && !isSlash) ? (settings?.busy_input_mode ?? 'interrupt') : undefined;
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

  if (cardVisible) return null;

  return (
    <div
      className={`composer-wrap ${isDragOver ? 'drag-over' : ''}`}
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
    >
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
        {/* Drop hint - only visible when dragging */}
        {isDragOver && (
          <div className="drop-hint">
            <Icon name="upload" size={32} />
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
              <Icon name="attach_file" size={18} />
              <span>{file.name}</span>
              <button onClick={() => handleRemoveFile(index)}>×</button>
            </div>
          ))}
        </div>
        
        {/* Textarea — wrapped so scrollbar is clipped to rounded top corners */}
        <div className="composer-textarea-wrap">
          <textarea
            ref={textareaRef}
            className="composer-textarea no-focus-ring"
          rows={1}
          placeholder={t('composer.placeholder')}
          value={input}
          onFocus={() => {
            const sid = currentSession?.session_id;
            if (sid) markSessionSeen(sid);
          }}
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
          disabled={cardVisible}
          />
        </div>

        {/* Footer */}
        <div className="composer-footer">
          <div className="composer-left">
            {/* Voice input button — Web Speech API */}
            {voiceSupported && (
              <button
                className={`icon-btn${voiceListening ? ' voice-active' : ''}`}
                onClick={handleVoiceToggle}
                title={voiceListening ? t('composer.voiceStop') : t('composer.voiceStart')}
                disabled={cardVisible}
              >
                <Icon name="mic" size={18} fill={voiceListening ? 1 : 0} />
              </button>
            )}
            <button
              className="icon-btn"
              onClick={handleAttachClick}
              title={t('composer.attach')}
              disabled={cardVisible}
            >
              <Icon name="attach_file" size={18} />
            </button>

            {/* Model */}
            <SingleSelection
              value={effectiveModel}
              trigger={
                <Icon name="monitor" size={18} />
              }
              triggerLabel={displayModel}
              optionGroups={modelGroups.map(g => ({
                header: g.provider,
                items: g.models.map(m => ({
                  value: m.id,
                  label: m.label,
                  description: m.id,
                })),
              }))}
              onSelect={handleModelSelect}              loading={modelsLoading}
              onOpen={loadModels}
              emptyText="No models available"
              header="Model"
            />

            {/* Reasoning */}
            <SingleSelection
              value={effectiveReasoningEffort}
              trigger={
                <Icon name="exercise" size={18} />
              }
              triggerLabel={t('reasoning.label')}
              disabled={!currentModelSupportsThinking}
              options={REASONING_EFFORT_LEVELS.map(l => ({ value: l, label: t(`reasoning.${l}`) }))}
              onSelect={handleReasoningSelect}              header={t('reasoning.label')}
            />

            {/* Personality */}
            <SingleSelection
              value={effectivePersonality}
              trigger={
                <Icon name="person" size={18} />
              }
              triggerLabel={effectivePersonality || 'Personality'}
              options={personalities.map(p => ({ value: p.name, label: p.name, description: p.description || '' }))}
              onSelect={handlePersonalitySelect}              header="Personality"
              showNone
            />

            {/* Workspace */}
            {!workspaceDisabled && (
              <SingleSelection
                value={currentWorkspacePath || null}
                trigger={
                  <Icon name="folder_open" size={18} />
                }
                triggerLabel={currentWorkspacePath ? effectiveWorkspaceName : 'Workspace'}
                showNone
                options={workspaces
                  .filter((ws: any) => !(isWebUISession && ws.external))
                  .map((ws: any) => ({
                    value: ws.path,
                    label: ws.external ? `${ws.name}(EXT)` : ws.name,
                  }))}
                onSelect={handleSelectWorkspace}                header="Workspace"
                emptyText="No workspaces added"
              />
            )}

            {/* Skills — multi-selection popover */}
            <MultiSelection
              selectedValues={selectedSkillValues}
              onChange={handleSkillsChange}
              trigger={
                <Icon name="layers" size={18} />
              }
              triggerLabel={skillsTriggerLabel}
              triggerClass="icon-btn"
              optionGroups={skillOptionGroups}
              onOpen={handleSkillsOpen}
              emptyText={t('session_skills.no_skills', { ns: 'common', defaultValue: 'No skills installed' })}
              header={t('session_skills.panel_title', { ns: 'common', defaultValue: 'Session Skills' })}
            />

            {/* Session approval indicator — shield icon, colored when active */}
            <ApprovalIndicator />

            <ContextUsageIndicator usage={lastUsage} session={currentSession} lastCompressionTime={lastCompressionTime} streaming={busy} />
          </div>

          {/* Mode selector — between model chip and send button */}
          <ModeSelector sessionId={currentSession?.session_id ?? null} />

          <div className="composer-right">
            {/* Streaming with no input text: show cancel button instead of send.
                As soon as the user starts typing, switch back to send so they can
                queue/steer/interrupt while the agent is running. */}
            {busy && !input.trim() ? (
              <button
                className="cancel-btn"
                onClick={onCancel}
                title={t('composer.stop')}
                aria-label={t('composer.stop')}
              >
                <Icon name="stop" size={14} fill={1} />
              </button>
            ) : (
              <button
                className="send-btn"
                onClick={handleSubmit}
                title={t('composer.send')}
                disabled={!canSend}
              >
                <Icon name="arrow_upward" size={18} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
