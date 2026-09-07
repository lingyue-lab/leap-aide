import React, { useEffect, useState, useCallback } from 'react';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useTheme } from '@/contexts/ThemeContext';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import type { Settings, Theme, SendKey } from '@/types';

interface SettingsModalProps {
  onClose: () => void;
}

const THEMES: { value: Theme; label: string }[] = [
  { value: 'dark',      label: 'Dark' },
  { value: 'light',     label: 'Light' },
  { value: 'system',    label: 'System' },
];

const LANGUAGES = [
  { value: 'en',    label: 'English' },
  { value: 'zh-CN', label: '中文 (简体)' },
];

export default function SettingsModal({ onClose }: SettingsModalProps) {
  const { settings, updateSettings, loadSettings } = useSettingsStore();
  const { setTheme } = useTheme();
  const [form, setForm] = useState<Partial<Settings>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load settings on mount if not already loaded
  useEffect(() => {
    if (!settings) {
      loadSettings();
    }
  }, [settings, loadSettings]);

  // Sync form when settings load
  useEffect(() => {
    if (settings) {
      setForm({ ...settings });
    }
  }, [settings]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const handleOverlayClick = useCallback((e: React.MouseEvent) => {
    if (e.target === e.currentTarget) onClose();
  }, [onClose]);

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setForm(prev => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await updateSettings(form as Settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError((err as Error).message || 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  if (!settings && !form.model) {
    return (
      <div className="settings-overlay" onClick={handleOverlayClick}>
        <div className="settings-panel" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 200 }}>
          <LoadingSpinner mode="inline" text="Loading settings…" />
        </div>
      </div>
    );
  }

  return (
    <div className="settings-overlay" onClick={handleOverlayClick}>
      <div className="settings-panel" role="dialog" aria-modal="true" aria-label="Settings">
        {/* Header */}
        <div className="settings-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 21 }}>⚙️</span>
            <div>
              <div style={{ fontWeight: 700, fontSize: 17, color: 'var(--text)' }}>Settings &amp; Preferences</div>
              <div style={{ fontSize: 14, color: 'var(--muted)', marginTop: 2 }}>Configure Hermes WebUI</div>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--muted)',
              padding: '4px 8px',
              borderRadius: 6,
              fontSize: 21,
              lineHeight: 1,
            }}
            title="Close"
            aria-label="Close settings"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="settings-body">
          {/* Appearance */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--muted)', marginBottom: 10 }}>
              Appearance
            </div>

            <div className="settings-field">
              <label htmlFor="s-theme">Theme</label>
              <select
                id="s-theme"
                value={form.theme ?? 'dark'}
                onChange={e => {
                  const t = e.target.value as Theme;
                  set('theme', t);
                  setTheme(t);
                }}
              >
                {THEMES.map(t => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>

            <div className="settings-field">
              <label htmlFor="s-language">Language</label>
              <select
                id="s-language"
                value={form.language ?? 'en'}
                onChange={e => set('language', e.target.value)}
              >
                {LANGUAGES.map(l => (
                  <option key={l.value} value={l.value}>{l.label}</option>
                ))}
              </select>
            </div>

          </div>

          {/* Model & Input */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--muted)', marginBottom: 10 }}>
              Model &amp; Input
            </div>

            <div className="settings-field">
              <label htmlFor="s-model">Default Model</label>
              <input
                id="s-model"
                type="text"
                value={form.model ?? ''}
                onChange={e => set('model', e.target.value)}
                placeholder="e.g. anthropic/claude-sonnet-4-5"
              />
            </div>

            <div className="settings-field">
              <label htmlFor="s-sendkey">Send Message Key</label>
              <select
                id="s-sendkey"
                value={form.send_key ?? 'enter'}
                onChange={e => set('send_key', e.target.value as SendKey)}
              >
                <option value="enter">Enter</option>
                <option value="ctrl+enter">Ctrl + Enter</option>
              </select>
            </div>

          </div>

          {/* Notifications & Display */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--muted)', marginBottom: 10 }}>
              Notifications &amp; Display
            </div>

            <div className="settings-field" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                type="checkbox"
                id="s-sound"
                checked={form.sound_enabled ?? true}
                onChange={e => set('sound_enabled', e.target.checked)}
              />
              <label htmlFor="s-sound" style={{ marginBottom: 0, textTransform: 'none', fontSize: 15, fontWeight: 500, color: 'var(--text)' }}>
                Sound effects
              </label>
            </div>

            <div className="settings-field" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                type="checkbox"
                id="s-notif"
                checked={form.notifications_enabled ?? false}
                onChange={e => set('notifications_enabled', e.target.checked)}
              />
              <label htmlFor="s-notif" style={{ marginBottom: 0, textTransform: 'none', fontSize: 15, fontWeight: 500, color: 'var(--text)' }}>
                Desktop notifications
              </label>
            </div>

            <div className="settings-field" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                type="checkbox"
                id="s-cli"
                checked={form.show_cli_sessions ?? true}
                onChange={e => set('show_cli_sessions', e.target.checked)}
              />
              <label htmlFor="s-cli" style={{ marginBottom: 0, textTransform: 'none', fontSize: 15, fontWeight: 500, color: 'var(--text)' }}>
                Show CLI sessions in sidebar
              </label>
            </div>

            <div className="settings-field" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                type="checkbox"
                id="s-messaging"
                checked={form.show_messaging_sessions ?? true}
                onChange={e => set('show_messaging_sessions', e.target.checked)}
              />
              <label htmlFor="s-messaging" style={{ marginBottom: 0, textTransform: 'none', fontSize: 15, fontWeight: 500, color: 'var(--text)' }}>
                Show messaging sessions in sidebar
              </label>
            </div>

            <div className="settings-field" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                type="checkbox"
                id="s-cron"
                checked={form.show_cron_sessions ?? false}
                onChange={e => set('show_cron_sessions', e.target.checked)}
              />
              <label htmlFor="s-cron" style={{ marginBottom: 0, textTransform: 'none', fontSize: 15, fontWeight: 500, color: 'var(--text)' }}>
                Show scheduled task sessions in sidebar
              </label>
            </div>

            <div className="settings-field" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <input
                type="checkbox"
                id="s-updates"
                checked={form.check_for_updates ?? true}
                onChange={e => set('check_for_updates', e.target.checked)}
              />
              <label htmlFor="s-updates" style={{ marginBottom: 0, textTransform: 'none', fontSize: 15, fontWeight: 500, color: 'var(--text)' }}>
                Check for updates
              </label>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div style={{ marginBottom: 12, padding: '8px 12px', background: 'rgba(239, 68, 68, 0.12)', border: '1px solid rgba(239, 68, 68, 0.3)', borderRadius: 8, color: '#f87171', fontSize: 15 }}>
              {error}
            </div>
          )}

          {/* Actions */}
          <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
            <button
              onClick={onClose}
              style={{
                padding: '8px 16px',
                borderRadius: 8,
                border: '1px solid var(--border2)',
                background: 'none',
                color: 'var(--muted)',
                cursor: 'pointer',
                fontSize: 15,
              }}
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              style={{
                padding: '8px 20px',
                borderRadius: 8,
                border: 'none',
                background: saved ? 'rgba(34, 197, 94, 0.8)' : 'var(--blue)',
                color: '#fff',
                cursor: saving ? 'not-allowed' : 'pointer',
                fontSize: 15,
                fontWeight: 600,
                opacity: saving ? 0.7 : 1,
                transition: 'background 0.2s',
              }}
            >
              {saving ? 'Saving…' : saved ? '✓ Saved' : 'Save Settings'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
