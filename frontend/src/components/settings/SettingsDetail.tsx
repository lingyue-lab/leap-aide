import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
// @ts-ignore – @monaco-editor/react is installed on the server; run npm install
import Editor from '@monaco-editor/react';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useTheme } from '@/contexts/ThemeContext';
import { configFileService, providersService } from '@/services/api';
import type { Settings, Theme, SendKey, FontSize, Provider } from '@/types';

// ─── Reusable field components ────────────────────────────────────────────────

function FieldRow({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, padding: '12px 0', borderBottom: '1px solid var(--border2)' }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)', lineHeight: 1.4 }}>{label}</div>
        {hint && <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 3, lineHeight: 1.4 }}>{hint}</div>}
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
        border: 'none',
        cursor: 'pointer',
        background: checked ? 'var(--blue)' : 'var(--border2)',
        position: 'relative',
        transition: 'background 0.2s',
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
          background: '#fff',
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
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        padding: '5px 28px 5px 10px',
        borderRadius: 6,
        border: '1px solid var(--border2)',
        background: 'var(--input-bg)',
        color: 'var(--text)',
        fontSize: 12,
        cursor: 'pointer',
        minWidth: 120,
        appearance: 'none',
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23888' stroke-width='1.5' fill='none'/%3E%3C/svg%3E")`,
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'right 8px center',
      }}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
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
        borderRadius: 6,
        border: '1px solid var(--border2)',
        background: 'var(--input-bg)',
        color: 'var(--text)',
        fontSize: 12,
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
    { value: 'slate',     label: t('settings.themes.slate') },
    { value: 'solarized', label: t('settings.themes.solarized') },
    { value: 'monokai',   label: t('settings.themes.monokai') },
    { value: 'nord',      label: t('settings.themes.nord') },
    { value: 'oled',      label: t('settings.themes.oled') },
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
    </>
  );
}

function ModelGroup({ form, set }: GroupProps) {
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
      <FieldRow label={t('settings.fields.bot_name')} hint={t('settings.fields.bot_name_hint')}>
        <TextInput
          value={form.bot_name ?? ''}
          onChange={(v) => set('bot_name', v)}
          placeholder="Hermes"
        />
      </FieldRow>
    </>
  );
}

function NotificationsGroup({ form, set }: GroupProps) {
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
      <FieldRow label={t('settings.fields.show_token_usage')} hint={t('settings.fields.show_token_usage_hint')}>
        <Toggle checked={form.show_token_usage ?? true} onChange={(v) => set('show_token_usage', v)} />
      </FieldRow>
      <FieldRow label={t('settings.fields.show_cli_sessions')} hint={t('settings.fields.show_cli_sessions_hint')}>
        <Toggle checked={form.show_cli_sessions ?? false} onChange={(v) => set('show_cli_sessions', v)} />
      </FieldRow>
      <FieldRow label={t('settings.fields.sync_insights')} hint={t('settings.fields.sync_insights_hint')}>
        <Toggle checked={form.sync_insights ?? false} onChange={(v) => set('sync_insights', v)} />
      </FieldRow>
    </>
  );
}

// ─── History Group ────────────────────────────────────────────────────────────

function HistoryGroup({ form, set }: GroupProps) {
  const { t } = useTranslation();
  const MSG_THRESHOLDS = [
    { value: '0',   label: t('common:disabled', 'Disabled') },
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
    return { label: src[provider.key_source] ?? provider.key_source, color: '#4ade80' };
  };

  const badge = keySourceBadge();

  return (
    <div style={{
      border: '1px solid var(--border2)',
      borderRadius: 8,
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
          background: hasKey ? '#4ade80' : 'var(--border2)',
          flexShrink: 0,
          border: `1px solid ${hasKey ? '#22c55e' : 'var(--border)'}`,
        }} />
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', flex: 1 }}>
          {provider.display_name}
        </span>
        {badge && (
          <span style={{ fontSize: 11, color: badge.color, background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border2)', borderRadius: 4, padding: '1px 6px' }}>
            {badge.label}
          </span>
        )}
      </div>

      {/* API key row — only for configurable providers */}
      {provider.configurable && (
        <div style={{ padding: '10px 14px', background: 'rgba(0,0,0,0.08)', display: 'flex', alignItems: 'center', gap: 8 }}>
          {hasKey ? (
            /* Has key: show masked display + clear button only */
            <>
              <span style={{
                flex: 1,
                padding: '5px 10px',
                fontSize: 12,
                fontFamily: 'monospace',
                color: 'var(--muted)',
                letterSpacing: 2,
              }}>
                {MASKED}
              </span>

              {/* Clear / delete icon */}
              <button
                onClick={handleDelete}
                disabled={deleting}
                title={t('settings.providers.clear_key')}
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: '#f87171', flexShrink: 0, opacity: deleting ? 0.5 : 1 }}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                  <path d="M10 11v6"/>
                  <path d="M14 11v6"/>
                  <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                </svg>
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
                  borderRadius: 6,
                  border: '1px solid var(--border2)',
                  background: 'var(--input-bg)',
                  color: 'var(--text)',
                  fontSize: 12,
                  fontFamily: 'monospace',
                  minWidth: 0,
                }}
              />

              {/* Save icon */}
              <button
                onClick={handleSave}
                disabled={saving || !apiKey.trim()}
                title={t('settings.providers.save_key')}
                style={{ background: 'none', border: 'none', cursor: apiKey.trim() ? 'pointer' : 'not-allowed', padding: 4, color: apiKey.trim() ? 'var(--blue)' : 'var(--border2)', flexShrink: 0, opacity: saving ? 0.5 : 1 }}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
                  <polyline points="17 21 17 13 7 13 7 21"/>
                  <polyline points="7 3 7 8 15 8"/>
                </svg>
              </button>
            </>
          )}
        </div>
      )}

      {/* Non-configurable hint */}
      {!provider.configurable && (
        <div style={{ padding: '6px 14px 8px', fontSize: 11, color: 'var(--muted)' }}>
          {provider.is_oauth
            ? t('settings.providers.oauth_hint')
            : t('settings.providers.not_configurable_hint')}
        </div>
      )}

      {/* Feedback message */}
      {feedback && (
        <div style={{ padding: '4px 14px 8px', fontSize: 11, color: feedback.type === 'ok' ? '#4ade80' : '#f87171' }}>
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
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 120, color: 'var(--muted)', fontSize: 13 }}>
        {t('common.loading')}
      </div>
    );
  }

  if (error) {
    return <div style={{ color: '#f87171', fontSize: 13 }}>{error}</div>;
  }

  // Sort: has_key first, then configurable, then alphabetical
  const sorted = [...providers].sort((a, b) => {
    if (a.has_key !== b.has_key) return a.has_key ? -1 : 1;
    if (a.configurable !== b.configurable) return a.configurable ? -1 : 1;
    return a.display_name.localeCompare(b.display_name);
  });

  return (
    <div>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 14, lineHeight: 1.5 }}>
        {t('settings.providers.hint')}
      </div>
      {sorted.map((p) => (
        <ProviderRow key={p.id} provider={p} />
      ))}
    </div>
  );
}

// ─── Monaco theme map ─────────────────────────────────────────────────────────
const MONACO_THEME: Record<string, string> = {
  dark:      'vs-dark',
  light:     'light',
  system:    'vs-dark',
  slate:     'vs-dark',
  solarized: 'vs-dark',
  monokai:   'vs-dark',
  nord:      'vs-dark',
  oled:      'vs-dark',
};

interface ConfigGroupProps {
  editing: boolean;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  editorRef: React.MutableRefObject<any>;
  onContentLoaded: (raw: string) => void;
  onLoadError: (msg: string) => void;
}

function ConfigGroup({ editing, editorRef, onContentLoaded, onLoadError }: ConfigGroupProps) {
  const { t } = useTranslation();
  const { settings } = useSettingsStore();
  const currentTheme = (settings as (Settings & { theme?: string }) | null)?.theme ?? 'dark';
  const monacoTheme = MONACO_THEME[currentTheme] ?? 'vs-dark';

  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    configFileService.getRaw()
      .then((raw) => { setContent(raw); onContentLoaded(raw); })
      .catch((e) => onLoadError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: 'var(--muted)', fontSize: 13 }}>
        {t('common.loading')}
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Monaco Editor */}
      <div style={{ flex: 1, border: '1px solid var(--border2)', borderRadius: '8px 8px 0 0', overflow: 'hidden', minHeight: 0 }}>
        <Editor
          height="100%"
          language="yaml"
          theme={monacoTheme}
          value={content}
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          onMount={(editor: any) => { editorRef.current = editor; }}
          options={{
            minimap: { enabled: false },
            fontSize: 13,
            lineNumbers: 'on',
            scrollBeyondLastLine: false,
            wordWrap: 'on',
            tabSize: 2,
            automaticLayout: true,
            readOnly: !editing,
          }}
        />
      </div>
      <div style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.5, padding: '6px 0 10px', flexShrink: 0 }}>
        {t('settings.config_file_hint', 'Editing ~/.hermes/config.yaml for the active profile. Changes take effect immediately after saving.')}
      </div>
    </div>
  );
}

function AboutGroup() {
  const { t } = useTranslation();
  const { settings } = useSettingsStore();
  const version = (settings as (Settings & { webui_version?: string }) | null)?.webui_version;

  return (
    <>
      <FieldRow label={t('settings.fields.version')}>
        <span style={{ fontSize: 12, color: 'var(--muted)', fontFamily: 'monospace' }}>
          {version ?? '—'}
        </span>
      </FieldRow>
      <FieldRow label={t('settings.fields.check_updates')} hint={t('settings.fields.check_updates_hint')}>
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>{t('settings.fields.managed_via_settings')}</span>
      </FieldRow>
      <div style={{ marginTop: 20, padding: '14px', background: 'rgba(255,255,255,0.03)', borderRadius: 10, border: '1px solid var(--border)' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>Hermes WebUI</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }} dangerouslySetInnerHTML={{ __html: t('settings.about_text') }} />
      </div>
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
  const [configEditing, setConfigEditing]   = useState(false);
  const [configSaving, setConfigSaving]     = useState(false);
  const [configSaved, setConfigSaved]       = useState(false);
  const [configError, setConfigError]       = useState<string | null>(null);
  const [configSource, setConfigSource]     = useState(''); // last-saved server content
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const configEditorRef = useRef<any>(null);

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
    setConfigError(null);
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
    setConfigEditing(true);
    setConfigError(null);
    setConfigSaved(false);
  };

  const handleConfigCancel = () => {
    configEditorRef.current?.setValue(configSource);
    setConfigEditing(false);
    setConfigError(null);
  };

  const handleConfigSave = async () => {
    const val = configEditorRef.current ? configEditorRef.current.getValue() : configSource;
    setConfigSaving(true);
    setConfigError(null);
    try {
      await configFileService.saveRaw(val);
      setConfigSource(val);
      setConfigSaved(true);
      setConfigEditing(false);
      setTimeout(() => setConfigSaved(false), 2500);
    } catch (e) {
      setConfigError((e as Error).message);
    } finally {
      setConfigSaving(false);
    }
  };

  const isAbout = selectedGroup === 'about';
  const isConfig = selectedGroup === 'config';
  const isProviders = selectedGroup === 'providers';

  return (
    <div className="settings-detail">
      {/* Header bar */}
      <div className="topbar">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="topbar-title">{t(`settings.groups.${selectedGroup}`)}</div>
          <div className="topbar-meta">{t('settings.preferences')}</div>
        </div>

        {/* Normal settings Save button */}
        {!isAbout && !isConfig && !isProviders && (
          <div style={{ flexShrink: 0 }}>
            {error && (
              <span style={{ fontSize: 12, color: '#f87171', marginRight: 12 }}>{error}</span>
            )}
            <button
              onClick={handleSave}
              disabled={saving}
              className="cron-btn run"
              style={{
                padding: '6px 16px',
                fontSize: 13,
                background: saved ? 'color-mix(in srgb, var(--blue) 30%, var(--surface))' : undefined,
                transition: 'background 0.2s',
                opacity: saving ? 0.7 : 1,
              }}
            >
              {saving ? t('settings.actions.saving') : saved ? t('settings.actions.saved') : t('settings.actions.save')}
            </button>
          </div>
        )}

        {/* Config File Edit / Cancel / Save buttons */}
        {isConfig && (
          <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
            {configError && (
              <span style={{ fontSize: 12, color: '#f87171' }}>{configError}</span>
            )}
            {configSaved && !configEditing && (
              <span style={{ fontSize: 12, color: 'var(--blue)' }}>{t('settings.actions.saved')}</span>
            )}
            {!configEditing ? (
              <button
                onClick={handleConfigEdit}
                className="cron-btn"
                style={{ padding: '6px 16px', fontSize: 13 }}
              >
                {t('common.edit')}
              </button>
            ) : (
              <>
                <button
                  onClick={handleConfigCancel}
                  disabled={configSaving}
                  className="cron-btn"
                  style={{ padding: '6px 14px', fontSize: 13, opacity: configSaving ? 0.6 : 1 }}
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleConfigSave}
                  disabled={configSaving}
                  className="cron-btn run"
                  style={{ padding: '6px 16px', fontSize: 13, transition: 'background 0.2s', opacity: configSaving ? 0.7 : 1 }}
                >
                  {configSaving ? t('settings.actions.saving') : t('settings.actions.save')}
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {/* Content */}
      {isConfig ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', padding: '8px 16px 0' }}>
          <ConfigGroup
            editing={configEditing}
            editorRef={configEditorRef}
            onContentLoaded={(raw) => setConfigSource(raw)}
            onLoadError={(msg) => setConfigError(msg)}
          />
        </div>
      ) : (
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 24px 24px' }}>
          <div style={{ maxWidth: 560, margin: '0 auto' }}>
            {selectedGroup === 'appearance' && <AppearanceGroup form={form} set={setField} />}
            {selectedGroup === 'model' && <ModelGroup form={form} set={setField} />}
            {selectedGroup === 'notifications' && <NotificationsGroup form={form} set={setField} />}
            {selectedGroup === 'history' && <HistoryGroup form={form} set={setField} />}
            {selectedGroup === 'about' && <AboutGroup />}
            {selectedGroup === 'providers' && <ProvidersGroup />}
          </div>
        </div>
      )}
    </div>
  );
}
