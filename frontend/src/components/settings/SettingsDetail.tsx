import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useTheme } from '@/contexts/ThemeContext';
import { configFileService, providersService } from '@/services/api';
import FileView from '@/components/shared/FileView';
import { EditIcon, Icon } from '@/components/common/Icons';
import type { Settings, Theme, SendKey, FontSize, Provider } from '@/types';

// ─── Reusable field components ────────────────────────────────────────────────

function FieldRow({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, padding: '12px 0', borderBottom: '1px solid var(--md-outline-variant)' }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 16, fontWeight: 500, color: 'var(--md-on-surface)', lineHeight: 1.4 }}>{label}</div>
        {hint && <div style={{ fontSize: 14, color: 'var(--md-on-surface-variant)', marginTop: 3, lineHeight: 1.4 }}>{hint}</div>}
      </div>
      <div style={{ flexShrink: 0 }}>{children}</div>
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      style={{
        width: 40,
        height: 22,
        borderRadius: 11,
        border: checked ? 'none' : '1px solid var(--md-outline)',
        cursor: 'pointer',
        background: checked ? 'var(--md-primary)' : 'var(--md-surface-container-highest)',
        position: 'relative',
        transition: 'background 0.2s, border-color 0.2s',
        flexShrink: 0,
      }}
    >
      <span
        style={{
          position: 'absolute',
          top: 3,
          left: checked ? 21 : 3,
          width: 16,
          height: 16,
          borderRadius: '50%',
          background: checked ? 'var(--md-on-primary)' : 'var(--md-outline)',
          transition: 'left 0.2s',
          boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
        }}
      />
    </button>
  );
}

function SelectField({ value, onChange, options }: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          padding: '5px 28px 5px 10px',
          borderRadius: 'var(--md-shape-xs)',
          border: '1px solid var(--md-outline)',
          background: 'var(--md-surface-container-highest)',
          color: 'var(--md-on-surface)',
          fontSize: 16,
          cursor: 'pointer',
          // 统一下限 200px（对齐 TextInput），上限 280px（M3 菜单 max-width）
          minWidth: 200,
          maxWidth: 280,
          appearance: 'none',
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <Icon
        name="keyboard_arrow_down"
        size={10}
        style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: 'var(--md-on-surface-variant)' }}
      />
    </div>
  );
}

function TextInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        padding: '5px 10px',
        borderRadius: 'var(--md-shape-xs)',
        border: '1px solid var(--md-outline)',
        background: 'var(--md-surface-container-highest)',
        color: 'var(--md-on-surface)',
        fontSize: 16,
        width: 200,
      }}
    />
  );
}

// ─── Groups ────────────────────────────────────────────────────────────────────

interface GroupProps {
  form: Partial<Settings>;
  set: <K extends keyof Settings>(key: K, value: Settings[K]) => void;
}

function AppearanceGroup({ form, set }: GroupProps) {
  const { t } = useTranslation();
  const { setTheme } = useTheme();
  
  const THEMES: { value: Theme; label: string }[] = [
    { value: 'dark',      label: t('settings.themes.dark') },
    { value: 'light',     label: t('settings.themes.light') },
    { value: 'system',    label: t('settings.themes.system') },
  ];

  const LANGUAGES = [
    { value: 'en',    label: 'English' },
    { value: 'zh-CN', label: '中文 (简体)' },
  ];

  const FONT_SIZES: { value: FontSize; label: string }[] = [
    { value: 'small',  label: t('settings.font_sizes.small') },
    { value: 'medium', label: t('settings.font_sizes.medium') },
    { value: 'large',  label: t('settings.font_sizes.large') },
  ];

  return (
    <>
      <FieldRow label={t('settings.fields.theme')} hint={t('settings.fields.theme_hint')}>
        <SelectField
          value={form.theme ?? 'dark'}
          onChange={(v) => {
            const th = v as Theme;
            set('theme', th);
            setTheme(th);
          }}
          options={THEMES}
        />
      </FieldRow>
      <FieldRow label={t('settings.fields.language')} hint={t('settings.fields.language_hint')}>
        <SelectField
          value={form.language ?? 'en'}
          onChange={(v) => set('language', v)}
          options={LANGUAGES}
        />
      </FieldRow>
      <FieldRow label={t('settings.fields.font_size')} hint={t('settings.fields.font_size_hint')}>
        <SelectField
          value={form.font_size ?? 'medium'}
          onChange={(v) => set('font_size', v as FontSize)}
          options={FONT_SIZES}
        />
      </FieldRow>
    </>
  );
}

function InputGroup({ form, set }: GroupProps) {
  const { t } = useTranslation();

  const SEND_KEYS: { value: SendKey; label: string }[] = [
    { value: 'enter',      label: t('settings.send_keys.enter') },
    { value: 'ctrl+enter', label: t('settings.send_keys.ctrl_enter') },
  ];

  return (
    <>
      <FieldRow label={t('settings.fields.send_key')} hint={t('settings.fields.send_key_hint')}>
        <SelectField
          value={form.send_key ?? 'enter'}
          onChange={(v) => set('send_key', v as SendKey)}
          options={SEND_KEYS}
        />
      </FieldRow>
      <FieldRow label={t('settings.fields.busy_input_mode')} hint={t('settings.fields.busy_input_mode_hint')}>
        <SelectField
          value={form.busy_input_mode ?? 'interrupt'}
          onChange={(v) => set('busy_input_mode', v as 'interrupt' | 'queue' | 'steer')}
          options={[
            { value: 'interrupt', label: t('settings.busy_input_modes.interrupt') },
            { value: 'queue', label: t('settings.busy_input_modes.queue') },
            { value: 'steer', label: t('settings.busy_input_modes.steer') },
          ]}
        />
      </FieldRow>
    </>
  );
}

function SessionsGroup({ form, set }: GroupProps) {
  const { t } = useTranslation();
  return (
    <>
      <FieldRow label={t('settings.fields.show_cli_sessions')} hint={t('settings.fields.show_cli_sessions_hint')}>
        <Toggle checked={form.show_cli_sessions ?? true} onChange={(v) => set('show_cli_sessions', v)} />
      </FieldRow>
      <FieldRow label={t('settings.fields.show_messaging_sessions')} hint={t('settings.fields.show_messaging_sessions_hint')}>
        <Toggle checked={form.show_messaging_sessions ?? true} onChange={(v) => set('show_messaging_sessions', v)} />
      </FieldRow>
      <FieldRow label={t('settings.fields.show_cron_sessions')} hint={t('settings.fields.show_cron_sessions_hint')}>
        <Toggle checked={form.show_cron_sessions ?? false} onChange={(v) => set('show_cron_sessions', v)} />
      </FieldRow>
      <FieldRow label={t('settings.fields.session_poll_interval')} hint={t('settings.fields.session_poll_interval_hint')}>
        <TextInput
          value={form.session_poll_interval_sec ?? '10'}
          onChange={(v) => set('session_poll_interval_sec', v)}
          placeholder="10"
        />
      </FieldRow>
      <FieldRow label={t('settings.fields.observer_tab_cleanup')} hint={t('settings.fields.observer_tab_cleanup_hint')}>
        <SelectField
          value={form.observer_tab_cleanup ?? 'session'}
          onChange={(v) => set('observer_tab_cleanup', v as 'session' | 'none')}
          options={[
            { value: 'session', label: t('settings.fields.observer_tab_cleanup_session') },
            { value: 'none', label: t('settings.fields.observer_tab_cleanup_none') },
          ]}
        />
      </FieldRow>
    </>
  );
}

function AgentGroup({ form, set }: GroupProps) {
  const { t } = useTranslation();

  // Global default reasoning effort (from config.yaml, not WebUI settings)
  const [globalEffort, setGlobalEffort] = useState<string>('');

  useEffect(() => {
    fetch('/api/reasoning', { credentials: 'include' })
      .then((r) => r.json())
      .then((d) => setGlobalEffort(d.reasoning_effort || ''))
      .catch(() => {});
  }, []);

  const handleGlobalEffortChange = async (value: string) => {
    setGlobalEffort(value);
    try {
      await fetch('/api/reasoning', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ effort: value || 'none' }),
        credentials: 'include',
      });
    } catch {
      // revert on failure
      fetch('/api/reasoning', { credentials: 'include' })
        .then((r) => r.json())
        .then((d) => setGlobalEffort(d.reasoning_effort || ''))
        .catch(() => {});
    }
  };

  const EFFORT_OPTIONS: { value: string; label: string }[] = [
    { value: '', label: t('chat:reasoning.default') },
    { value: 'none', label: t('chat:reasoning.none') },
    { value: 'minimal', label: t('chat:reasoning.minimal') },
    { value: 'low', label: t('chat:reasoning.low') },
    { value: 'medium', label: t('chat:reasoning.medium') },
    { value: 'high', label: t('chat:reasoning.high') },
    { value: 'xhigh', label: t('chat:reasoning.xhigh') },
  ];

  return (
    <>
      <FieldRow label={t('settings.fields.default_reasoning_effort')} hint={t('settings.fields.default_reasoning_effort_hint')}>
        <SelectField
          value={globalEffort}
          onChange={handleGlobalEffortChange}
          options={EFFORT_OPTIONS}
        />
      </FieldRow>
      <FieldRow label={t('settings.fields.default_mode')} hint={t('settings.fields.default_mode_hint')}>
        <SelectField
          value={form.default_mode ?? 'manual'}
          onChange={(v) => set('default_mode', v as 'manual' | 'smart' | 'yolo')}
          options={[
            { value: 'manual', label: t('mode.manual', { ns: 'chat' }) },
            { value: 'smart', label: t('mode.smart', { ns: 'chat' }) },
            { value: 'yolo', label: t('mode.yolo', { ns: 'chat' }) },
          ]}
        />
      </FieldRow>
      <FieldRow label={t('settings.fields.approval_timeout')} hint={t('settings.fields.approval_timeout_hint')}>
        <TextInput
          value={form.approval_timeout_sec ?? '60'}
          onChange={(v) => set('approval_timeout_sec', v)}
          placeholder="60"
        />
      </FieldRow>
      <FieldRow label={t('settings.fields.clarify_timeout')} hint={t('settings.fields.clarify_timeout_hint')}>
        <TextInput
          value={form.clarify_timeout_sec ?? '600'}
          onChange={(v) => set('clarify_timeout_sec', v)}
          placeholder="600"
        />
      </FieldRow>
    </>
  );
}

function OtherGroup({ form, set }: GroupProps) {
  const { t } = useTranslation();
  return (
    <>
      <FieldRow label={t('settings.fields.sound_effects')} hint={t('settings.fields.sound_effects_hint')}>
        <Toggle checked={form.sound_enabled ?? true} onChange={(v) => set('sound_enabled', v)} />
      </FieldRow>
      <FieldRow label={t('settings.fields.desktop_notifications')} hint={t('settings.fields.desktop_notifications_hint')}>
        <Toggle
          checked={form.notifications_enabled ?? false}
          onChange={async (v) => {
            if (v && typeof Notification !== 'undefined' && Notification.permission !== 'granted') {
              const permission = await Notification.requestPermission();
              if (permission !== 'granted') return; // user denied — don't save the toggle
            }
            set('notifications_enabled', v);
          }}
        />
      </FieldRow>
      <FieldRow label={t('settings.fields.session_ttl')} hint={t('settings.fields.session_ttl_hint')}>
        <SelectField
          value={form.session_ttl ?? '86400'}
          onChange={(v) => set('session_ttl', v)}
          options={[
            { value: '3600',    label: '1 hour' },
            { value: '14400',   label: '4 hours' },
            { value: '43200',   label: '12 hours' },
            { value: '86400',   label: '24 hours' },
            { value: '604800',  label: '7 days' },
            { value: '2592000', label: '30 days' },
          ]}
        />
      </FieldRow>
    </>
  );
}

// ─── History Group ────────────────────────────────────────────────────────────

function HistoryGroup({ form, set }: GroupProps) {
  const { t } = useTranslation();
  const MSG_THRESHOLDS = [
    { value: '0',   label: t('common:disabled', 'Disabled') },
    { value: '30',  label: '30 messages' },
    { value: '50',  label: '50 messages' },
    { value: '100', label: '100 messages' },
    { value: '200', label: '200 messages' },
    { value: '500', label: '500 messages' },
  ];
  const TOKEN_THRESHOLDS = [
    { value: '0',    label: t('common:disabled', 'Disabled') },
    { value: '0.05', label: '5% of context' },
    { value: '0.50', label: '50% of context' },
    { value: '0.60', label: '60% of context' },
    { value: '0.70', label: '70% of context' },
    { value: '0.85', label: '85% of context' },
  ];
  const POOR_RATIOS = [
    { value: '0.25', label: '25% retained' },
    { value: '0.5',  label: '50% retained' },
    { value: '0.6',  label: '60% retained' },
    { value: '0.7',  label: '70% retained' },
    { value: '0.8',  label: '80% retained' },
  ];
  const DISABLE_AFTER = [
    { value: '1',  label: '1 poor attempt' },
    { value: '2',  label: '2 poor attempts' },
    { value: '3',  label: '3 poor attempts' },
    { value: '5',  label: '5 poor attempts' },
    { value: '10', label: '10 poor attempts' },
  ];
  return (
    <>
      <FieldRow
        label={t('settings.fields.auto_compress_msg_threshold')}
        hint={t('settings.fields.auto_compress_msg_threshold_hint')}
      >
        <SelectField
          value={String(form.auto_compress_threshold ?? '0')}
          onChange={(v) => set('auto_compress_threshold', v)}
          options={MSG_THRESHOLDS}
        />
      </FieldRow>
      <FieldRow
        label={t('settings.fields.auto_compress_token_threshold')}
        hint={t('settings.fields.auto_compress_token_threshold_hint')}
      >
        <SelectField
          value={String(form.auto_compress_token_threshold ?? '0')}
          onChange={(v) => set('auto_compress_token_threshold', v)}
          options={TOKEN_THRESHOLDS}
        />
      </FieldRow>
      <FieldRow
        label={t('settings.fields.auto_compress_poor_ratio')}
        hint={t('settings.fields.auto_compress_poor_ratio_hint')}
      >
        <SelectField
          value={String(form.auto_compress_poor_ratio ?? '0.5')}
          onChange={(v) => set('auto_compress_poor_ratio', v)}
          options={POOR_RATIOS}
        />
      </FieldRow>
      <FieldRow
        label={t('settings.fields.auto_compress_disable_after')}
        hint={t('settings.fields.auto_compress_disable_after_hint')}
      >
        <SelectField
          value={String(form.auto_compress_disable_after ?? '3')}
          onChange={(v) => set('auto_compress_disable_after', v)}
          options={DISABLE_AFTER}
        />
      </FieldRow>
      <FieldRow
        label={t('settings.fields.max_compaction_snapshots')}
        hint={t('settings.fields.max_compaction_snapshots_hint')}
      >
        <SelectField
          value={String(form.max_compaction_snapshots ?? '5')}
          onChange={(v) => set('max_compaction_snapshots', v)}
          options={[
            { value: '3',  label: '3 snapshots' },
            { value: '5',  label: '5 snapshots' },
            { value: '10', label: '10 snapshots' },
            { value: '20', label: '20 snapshots' },
          ]}
        />
      </FieldRow>
    </>
  );
}

// ─── Providers Group ──────────────────────────────────────────────────────────

function ProviderRow({ provider }: { provider: Provider }) {
  const { t } = useTranslation();
  // has_key: key already stored — show masked display, input locked
  // no key: input open, user can type and save directly
  const MASKED = '••••••••••••••••';
  const [apiKey, setApiKey] = useState('');
  const [hasKey, setHasKey] = useState(provider.has_key);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null);

  const flashFeedback = (type: 'ok' | 'err', msg: string) => {
    setFeedback({ type, msg });
    setTimeout(() => setFeedback(null), 3000);
  };

  const handleSave = async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    try {
      const res = await providersService.setKey(provider.id, apiKey.trim());
      if (res.ok) {
        flashFeedback('ok', t('settings.providers.saved'));
        setApiKey('');
        setHasKey(true);
      } else {
        flashFeedback('err', res.error ?? 'Failed');
      }
    } catch (e) {
      flashFeedback('err', (e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      const res = await providersService.deleteKey(provider.id);
      if (res.ok) {
        flashFeedback('ok', t('settings.providers.cleared'));
        setApiKey('');
        setHasKey(false);
      } else {
        flashFeedback('err', res.error ?? 'Failed');
      }
    } catch (e) {
      flashFeedback('err', (e as Error).message);
    } finally {
      setDeleting(false);
    }
  };

  const keySourceBadge = () => {
    if (provider.is_oauth) return null;
    if (!hasKey) return { label: t('settings.providers.no_key'), color: 'var(--muted)' };
    const src: Record<string, string> = {
      env_file: '.env',
      env_var: 'env',
      config_yaml: 'config.yaml',
      oauth: 'OAuth',
    };
    return { label: src[provider.key_source] ?? provider.key_source, color: 'var(--md-success)' };
  };

  const badge = keySourceBadge();

  return (
    <div style={{
      border: '1px solid var(--md-outline)',
      borderRadius: 'var(--md-shape-sm)',
      marginBottom: 8,
      overflow: 'hidden',
    }}>
      {/* Provider header row */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '10px 14px',
        background: 'var(--surface)',
      }}>
        {/* Status dot */}
        <span style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: hasKey ? 'var(--md-success)' : 'var(--md-outline)',
          flexShrink: 0,
          border: `1px solid ${hasKey ? 'var(--md-success)' : 'var(--md-outline-variant)'}`,
        }} />
        <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--md-on-surface)', flex: 1 }}>
          {provider.display_name}
        </span>
        {badge && (
          <span style={{ fontSize: 14, color: badge.color, background: 'var(--md-state-hover)', border: '1px solid var(--md-outline)', borderRadius: 'var(--md-shape-xs)', padding: '1px 6px' }}>
            {badge.label}
          </span>
        )}
      </div>

      {/* API key row — only for configurable providers */}
      {provider.configurable && (
        <div style={{ padding: '10px 14px', background: 'var(--md-surface-container-high)', display: 'flex', alignItems: 'center', gap: 8 }}>
          {hasKey ? (
            /* Has key: show masked display + clear button only */
            <>
              <span style={{
                flex: 1,
                padding: '5px 10px',
                fontSize: 16,
                fontFamily: 'monospace',
                color: 'var(--md-on-surface-variant)',
                letterSpacing: 2,
              }}>
                {MASKED}
              </span>

              {/* Clear / delete icon */}
              <button
                onClick={handleDelete}
                disabled={deleting}
                title={t('settings.providers.clear_key')}
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--md-error)', flexShrink: 0, opacity: deleting ? 0.5 : 1 }}
              >
                <Icon name="delete" size={17} />
              </button>
            </>
          ) : (
            /* No key: open input + save button */
            <>
              <input
                type="text"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); }}
                placeholder={t('settings.providers.enter_key')}
                style={{
                  flex: 1,
                  padding: '5px 10px',
                  borderRadius: 'var(--md-shape-xs)',
                  border: '1px solid var(--md-outline)',
                  background: 'var(--md-surface-container-highest)',
                  color: 'var(--md-on-surface)',
                  fontSize: 16,
                  fontFamily: 'monospace',
                  minWidth: 0,
                }}
              />

              {/* Save icon */}
              <button
                onClick={handleSave}
                disabled={saving || !apiKey.trim()}
                title={t('settings.providers.save_key')}
                style={{ background: 'none', border: 'none', cursor: apiKey.trim() ? 'pointer' : 'not-allowed', padding: 4, color: apiKey.trim() ? 'var(--md-primary)' : 'var(--md-outline)', flexShrink: 0, opacity: saving ? 0.5 : 1 }}
              >
                <Icon name="save" size={17} />
              </button>
            </>
          )}
        </div>
      )}

      {/* Non-configurable hint */}
      {!provider.configurable && (
        <div style={{ padding: '6px 14px 8px', fontSize: 14, color: 'var(--md-on-surface-variant)' }}>
          {provider.is_oauth
            ? t('settings.providers.oauth_hint')
            : t('settings.providers.not_configurable_hint')}
        </div>
      )}

      {/* Feedback message */}
      {feedback && (
        <div style={{ padding: '4px 14px 8px', fontSize: 14, color: feedback.type === 'ok' ? 'var(--md-success)' : 'var(--md-error)' }}>
          {feedback.msg}
        </div>
      )}
    </div>
  );
}

function ProvidersGroup() {
  const { t } = useTranslation();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    providersService.getProviders()
      .then((res) => setProviders(res.providers ?? []))
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 120, color: 'var(--md-on-surface-variant)', fontSize: 16 }}>
        {t('common.loading')}
      </div>
    );
  }

  if (error) {
    return <div style={{ color: 'var(--md-error)', fontSize: 16 }}>{error}</div>;
  }

  // Sort: has_key first, then configurable, then alphabetical
  const sorted = [...providers].sort((a, b) => {
    if (a.has_key !== b.has_key) return a.has_key ? -1 : 1;
    if (a.configurable !== b.configurable) return a.configurable ? -1 : 1;
    return a.display_name.localeCompare(b.display_name);
  });

  return (
    <div>
      <div style={{ fontSize: 14, color: 'var(--md-on-surface-variant)', marginBottom: 14, lineHeight: 1.5 }}>
        {t('settings.providers.hint')}
      </div>
      {sorted.map((p) => (
        <ProviderRow key={p.id} provider={p} />
      ))}
    </div>
  );
}

interface ConfigGroupProps {
  editing: boolean;
  editContent: string;
  onEditChange: (v: string) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
  saveError: string | null;
  loadError: string | null;
  onContentLoaded: (raw: string) => void;
  onLoadError: (msg: string) => void;
}

function ConfigGroup({ editing, editContent, onEditChange, onSave, onCancel, saving, saveError, loadError, onContentLoaded, onLoadError }: ConfigGroupProps) {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    configFileService.getRaw()
      .then((raw) => { setContent(raw); onContentLoaded(raw); })
      .catch((e) => onLoadError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <FileView
      content={content}
      fileType="text"
      filePath="config.yaml"
      loading={loading}
      error={loadError || undefined}
      editing={editing}
      editContent={editContent}
      onEditChange={onEditChange}
      onSave={onSave}
      onCancel={onCancel}
      saving={saving}
      editError={saveError || undefined}
    />
  );
}

function AboutGroup() {
  const { t } = useTranslation();
  const { settings } = useSettingsStore();
  const version = (settings as (Settings & { webui_version?: string }) | null)?.webui_version;

  return (
    <>
      <FieldRow label={t('settings.fields.version')}>
        <span style={{ fontSize: 14, color: 'var(--md-on-surface-variant)', fontFamily: 'monospace' }}>
          {version ?? '—'}
        </span>
      </FieldRow>
      <FieldRow label={t('settings.fields.license')}>
        <span style={{ fontSize: 14, color: 'var(--md-on-surface-variant)', fontFamily: 'monospace' }}>
          MIT License
        </span>
      </FieldRow>
    </>
  );
}

// ─── Group label map ───────────────────────────────────────────────────────────

// ─── Main component ────────────────────────────────────────────────────────────

export default function SettingsDetail() {
  const { t } = useTranslation();
  const selectedGroup = useSettingsTabStore((s) => s.selectedGroup);
  const { settings, updateSettings, loadSettings } = useSettingsStore();
  const [form, setForm] = useState<Partial<Settings>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Config group state (lifted from ConfigGroup) ──────────────────────────
  const [configEditing, setConfigEditing]     = useState(false);
  const [configSaving, setConfigSaving]       = useState(false);
  const [configSaved, setConfigSaved]         = useState(false);
  const [configLoadError, setConfigLoadError] = useState<string | null>(null);
  const [configSaveError, setConfigSaveError] = useState<string | null>(null);
  const [configSource, setConfigSource]       = useState(''); // last-saved server content
  const [configEditContent, setConfigEditContent] = useState(''); // draft while editing

  useEffect(() => {
    if (!settings) loadSettings();
  }, []);

  useEffect(() => {
    if (settings) setForm({ ...settings });
  }, [settings]);

  // Reset saved feedback when group changes
  useEffect(() => {
    setSaved(false);
    setError(null);
    setConfigEditing(false);
    setConfigLoadError(null);
    setConfigSaveError(null);
    setConfigEditContent('');
    setConfigSaved(false);
  }, [selectedGroup]);

  const setField = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await updateSettings(form as Settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError((err as Error).message || 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  const handleConfigEdit = () => {
    setConfigEditContent(configSource);
    setConfigEditing(true);
    setConfigSaveError(null);
    setConfigSaved(false);
  };

  const handleConfigCancel = () => {
    setConfigEditing(false);
    setConfigEditContent('');
    setConfigSaveError(null);
  };

  const handleConfigSave = async () => {
    setConfigSaving(true);
    setConfigSaveError(null);
    try {
      await configFileService.saveRaw(configEditContent);
      setConfigSource(configEditContent);
      setConfigSaved(true);
      setConfigEditing(false);
      setTimeout(() => setConfigSaved(false), 2500);
    } catch (e) {
      setConfigSaveError((e as Error).message);
    } finally {
      setConfigSaving(false);
    }
  };

  const isAbout = selectedGroup === 'about';
  const isConfig = selectedGroup === 'config';
  const isProviders = selectedGroup === 'providers';

  return (
    <div className="settings-detail">
      {/* Header bar — hidden for config while editing (EditorView provides its own toolbar) */}
      {!(isConfig && configEditing) && (
        <div className="topbar">
          <div className="flex-1 min-w-0 overflow-hidden">
            <div className="topbar-title">{t(`settings.groups.${selectedGroup}`)}</div>
            <div className="topbar-meta">{t('settings.preferences')}</div>
          </div>

          {isConfig ? (
            <div className="flex items-center gap-2">
              {configSaved && (
                <span className="text-sm text-[var(--md-primary)]">{t('settings.actions.saved')}</span>
              )}
              <button
                className="icon-btn"
                onClick={handleConfigEdit}
                title={t('common.edit')}
              >
                <EditIcon />
              </button>
            </div>
          ) : (
            /* Normal settings Save button */
            !isAbout && !isProviders && (
              <div className="topbar-chips">
                {error && (
                  <span className="text-sm text-[var(--md-error)]">{error}</span>
                )}
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="cron-btn run text-sm"
                  style={{
                    background: saved ? 'var(--md-primary-container)' : undefined,
                    color: saved ? 'var(--md-on-primary-container)' : undefined,
                    opacity: saving ? 0.7 : 1,
                  }}
                >
                  {saving ? t('settings.actions.saving') : saved ? t('settings.actions.saved') : t('settings.actions.save')}
                </button>
              </div>
            )
          )}
        </div>
      )}

      {/* Content */}
      {isConfig ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          <ConfigGroup
            editing={configEditing}
            editContent={configEditContent}
            onEditChange={setConfigEditContent}
            onSave={handleConfigSave}
            onCancel={handleConfigCancel}
            saving={configSaving}
            saveError={configSaveError}
            loadError={configLoadError}
            onContentLoaded={(raw) => setConfigSource(raw)}
            onLoadError={(msg) => setConfigLoadError(msg)}
          />
        </div>
      ) : (
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 24px 24px' }}>
          <div style={{ maxWidth: 560, margin: '0 auto' }}>
            {selectedGroup === 'appearance' && <AppearanceGroup form={form} set={setField} />}
            {selectedGroup === 'input' && <InputGroup form={form} set={setField} />}
            {selectedGroup === 'sessions' && <SessionsGroup form={form} set={setField} />}
            {selectedGroup === 'agent' && <AgentGroup form={form} set={setField} />}
            {selectedGroup === 'other' && <OtherGroup form={form} set={setField} />}
            {selectedGroup === 'history' && <HistoryGroup form={form} set={setField} />}
            {selectedGroup === 'about' && <AboutGroup />}
            {selectedGroup === 'providers' && <ProvidersGroup />}
          </div>
        </div>
      )}
    </div>
  );
}
