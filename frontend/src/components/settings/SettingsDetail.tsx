import React, { useEffect, useState } from 'react';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import type { Settings, Theme, SendKey } from '@/types';

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

const THEMES: { value: Theme; label: string }[] = [
  { value: 'dark',      label: 'Dark' },
  { value: 'light',     label: 'Light' },
  { value: 'system',    label: 'System' },
  { value: 'slate',     label: 'Slate' },
  { value: 'solarized', label: 'Solarized' },
  { value: 'monokai',   label: 'Monokai' },
  { value: 'nord',      label: 'Nord' },
  { value: 'oled',      label: 'OLED Black' },
];

const LANGUAGES = [
  { value: 'en',    label: 'English' },
  { value: 'zh-CN', label: '中文 (简体)' },
];

const SEND_KEYS: { value: SendKey; label: string }[] = [
  { value: 'enter',      label: 'Enter' },
  { value: 'ctrl+enter', label: 'Ctrl + Enter' },
];

interface GroupProps {
  form: Partial<Settings>;
  set: <K extends keyof Settings>(key: K, value: Settings[K]) => void;
}

function AppearanceGroup({ form, set }: GroupProps) {
  return (
    <>
      <FieldRow label="Theme" hint="Color scheme for the interface">
        <SelectField
          value={form.theme ?? 'dark'}
          onChange={(v) => set('theme', v as Theme)}
          options={THEMES}
        />
      </FieldRow>
      <FieldRow label="Language" hint="Interface language">
        <SelectField
          value={form.language ?? 'en'}
          onChange={(v) => set('language', v)}
          options={LANGUAGES}
        />
      </FieldRow>
      <FieldRow label="Bubble layout" hint="Show messages in chat bubbles instead of full-width">
        <Toggle checked={form.bubble_layout ?? false} onChange={(v) => set('bubble_layout', v)} />
      </FieldRow>
    </>
  );
}

function ModelGroup({ form, set }: GroupProps) {
  return (
    <>
      <FieldRow label="Default Model" hint="Model used for new conversations">
        <TextInput
          value={form.model ?? ''}
          onChange={(v) => set('model', v)}
          placeholder="e.g. anthropic/claude-sonnet-4-5"
        />
      </FieldRow>
      <FieldRow label="Send Message Key" hint="Key combination to submit a message">
        <SelectField
          value={form.send_key ?? 'enter'}
          onChange={(v) => set('send_key', v as SendKey)}
          options={SEND_KEYS}
        />
      </FieldRow>
      <FieldRow label="Bot Name" hint="Display name shown for the assistant">
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
  return (
    <>
      <FieldRow label="Sound effects" hint="Play sounds on message receive">
        <Toggle checked={form.sound_enabled ?? true} onChange={(v) => set('sound_enabled', v)} />
      </FieldRow>
      <FieldRow label="Desktop notifications" hint="Show OS-level notifications">
        <Toggle checked={form.notifications_enabled ?? false} onChange={(v) => set('notifications_enabled', v)} />
      </FieldRow>
      <FieldRow label="Show token usage" hint="Display token count and estimated cost">
        <Toggle checked={form.show_token_usage ?? true} onChange={(v) => set('show_token_usage', v)} />
      </FieldRow>
      <FieldRow label="Show CLI sessions" hint="Include command-line sessions in the sidebar">
        <Toggle checked={form.show_cli_sessions ?? false} onChange={(v) => set('show_cli_sessions', v)} />
      </FieldRow>
      <FieldRow label="Sync insights" hint="Enable cross-session insight syncing">
        <Toggle checked={form.sync_insights ?? false} onChange={(v) => set('sync_insights', v)} />
      </FieldRow>
    </>
  );
}

function AboutGroup() {
  const { settings } = useSettingsStore();
  const version = (settings as (Settings & { webui_version?: string }) | null)?.webui_version;

  return (
    <>
      <FieldRow label="Version">
        <span style={{ fontSize: 12, color: 'var(--muted)', fontFamily: 'monospace' }}>
          {version ?? '—'}
        </span>
      </FieldRow>
      <FieldRow label="Check for updates" hint="Automatically check for new versions">
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>Managed via settings</span>
      </FieldRow>
      <div style={{ marginTop: 20, padding: '14px', background: 'rgba(255,255,255,0.03)', borderRadius: 10, border: '1px solid var(--border)' }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 6 }}>Hermes WebUI</div>
        <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }}>
          An open-source web interface for AI coding assistants.<br />
          Built with React + TypeScript + Python.
        </div>
      </div>
    </>
  );
}

// ─── Group label map ───────────────────────────────────────────────────────────

const GROUP_LABELS: Record<string, string> = {
  appearance: 'Appearance',
  model: 'Model & Input',
  notifications: 'Notifications',
  about: 'About',
};

// ─── Main component ────────────────────────────────────────────────────────────

export default function SettingsDetail() {
  const selectedGroup = useSettingsTabStore((s) => s.selectedGroup);
  const { settings, updateSettings, loadSettings } = useSettingsStore();
  const [form, setForm] = useState<Partial<Settings>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  const isAbout = selectedGroup === 'about';

  return (
    <div className="settings-detail">
      {/* Header bar */}
      <div className="topbar">
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="topbar-title">{GROUP_LABELS[selectedGroup] ?? 'Settings'}</div>
          <div className="topbar-meta">Settings &amp; Preferences</div>
        </div>
        {!isAbout && (
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
                background: saved ? 'rgba(34, 197, 94, 0.8)' : undefined,
                transition: 'background 0.2s',
                opacity: saving ? 0.7 : 1,
              }}
            >
              {saving ? 'Saving…' : saved ? '✓ Saved' : 'Save'}
            </button>
          </div>
        )}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 24px 24px' }}>
        <div style={{ maxWidth: 560, margin: '0 auto' }}>
          {selectedGroup === 'appearance' && <AppearanceGroup form={form} set={setField} />}
          {selectedGroup === 'model' && <ModelGroup form={form} set={setField} />}
          {selectedGroup === 'notifications' && <NotificationsGroup form={form} set={setField} />}
          {selectedGroup === 'about' && <AboutGroup />}
        </div>
      </div>
    </div>
  );
}
