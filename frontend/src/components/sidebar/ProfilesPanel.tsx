import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { profilesService } from '@/services/api';
import type { Profile } from '@/types';

export default function ProfilesPanel() {
  const { t } = useTranslation('common');
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [activeProfile, setActiveProfile] = useState<string>('default');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState('');

  // Form state
  const [formName, setFormName] = useState('');
  const [cloneFrom, setCloneFrom] = useState<string>('');
  const [cloneConfig, setCloneConfig] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');

  useEffect(() => {
    loadProfiles();
  }, []);

  const loadProfiles = async () => {
    try {
      setLoading(true);
      const data = await profilesService.list();
      setProfiles(data.profiles);
      setActiveProfile(data.active);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load profiles');
    } finally {
      setLoading(false);
    }
  };

  const toggleForm = () => {
    setShowForm(!showForm);
    resetForm();
  };

  const resetForm = () => {
    setFormName('');
    setCloneFrom('');
    setCloneConfig(false);
    setBaseUrl('');
    setApiKey('');
    setFormError('');
  };

  const handleSubmit = async () => {
    if (!formName.trim()) {
      setFormError('Profile name is required');
      return;
    }

    try {
      await profilesService.create(
        formName.trim(),
        cloneFrom.trim() || undefined,
        cloneConfig,
        baseUrl.trim() || undefined,
        apiKey.trim() || undefined
      );
      loadProfiles();
      toggleForm();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to create profile');
    }
  };

  const handleSwitch = async (name: string) => {
    if (name === activeProfile) return;
    try {
      const result = await profilesService.switch(name) as { profiles: Profile[]; active: string };
      setProfiles(result.profiles);
      setActiveProfile(result.active);
    } catch (err) {
      console.error('Failed to switch profile:', err);
    }
  };

  const handleDelete = async (name: string) => {
    if (name === 'default') return;
    if (!confirm(t('profiles.confirm_delete') || 'Delete this profile?')) return;
    try {
      await profilesService.delete(name);
      loadProfiles();
    } catch (err) {
      console.error('Failed to delete profile:', err);
    }
  };

  if (loading) {
    return (
      <div className="p-3 text-center text-[var(--muted)] text-sm">
        {t('common.loading') || 'Loading...'}
      </div>
    );
  }

  return (
    <div className="profiles-panel">
      {/* Header */}
      <div className="flex items-center justify-between p-2 border-b border-[var(--border)]">
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
          {t('profiles.title') || 'Profiles'}
        </span>
        <button
          className="cron-btn run text-xs px-2 py-1"
          onClick={toggleForm}
        >
          + {t('profiles.new') || 'New Profile'}
        </button>
      </div>

      {/* Create Form */}
      {showForm && (
        <div className="profile-form p-2 border-b border-[var(--border)]">
          <input
            type="text"
            placeholder="Profile name (lowercase, a-z 0-9 hyphens)"
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
          />
          <label className="flex items-center gap-2 text-xs text-[var(--muted)] mb-2">
            <input
              type="checkbox"
              checked={cloneConfig}
              onChange={(e) => setCloneConfig(e.target.checked)}
              className="accent-[var(--accent)]"
            />
            {t('profiles.clone_config') || 'Clone config from active profile'}
          </label>
          {cloneConfig && profiles.length > 0 && (
            <select
              value={cloneFrom}
              onChange={(e) => setCloneFrom(e.target.value)}
              className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
            >
              <option value="">{t('profiles.clone_active') || 'Clone from active'}</option>
              {profiles.filter(p => p.name !== 'default').map((p) => (
                <option key={p.name} value={p.name}>{p.name}</option>
              ))}
            </select>
          )}
          <input
            type="text"
            placeholder="Base URL (optional, e.g. http://localhost:11434)"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
          />
          <input
            type="password"
            placeholder="API key (optional)"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
          />
          <div className="flex gap-2">
            <button
              className="cron-btn run flex-1 text-xs py-1"
              onClick={handleSubmit}
            >
              {t('profiles.create') || 'Create'}
            </button>
            <button
              className="cron-btn flex-1 text-xs py-1"
              onClick={toggleForm}
            >
              {t('common.cancel') || 'Cancel'}
            </button>
          </div>
          {formError && (
            <div className="text-xs text-[var(--accent)] mt-2">{formError}</div>
          )}
        </div>
      )}

      {/* Profile List */}
      <div className="profile-list flex-1 overflow-y-auto p-2">
        {error && (
          <div className="text-sm text-[var(--accent)] mb-2">{error}</div>
        )}
        {profiles.length === 0 && !error && (
          <div className="text-sm text-[var(--muted)] text-center italic py-4">
            {t('profiles.empty') || 'No profiles'}
          </div>
        )}
        {profiles.map((profile) => (
          <div
            key={profile.name}
            className={`profile-item p-2 mb-2 rounded-lg border ${
              profile.is_active
                ? 'border-[var(--accent)] bg-[var(--accent)]/10'
                : 'border-[var(--border2)]'
            }`}
          >
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="profile-name text-sm font-medium">
                    {profile.name}
                  </span>
                  {profile.is_default && (
                    <span className="text-[10px] text-[var(--muted)] bg-[var(--border)] px-1 py-0.5 rounded">
                      {t('profiles.default') || 'default'}
                    </span>
                  )}
                  {profile.is_active && (
                    <span className="text-[10px] text-[var(--accent)] bg-[var(--accent)]/20 px-1 py-0.5 rounded">
                      {t('profiles.active') || 'active'}
                    </span>
                  )}
                </div>
                <div className="text-xs text-[var(--muted)] mt-1 flex flex-wrap gap-2">
                  {profile.model && (
                    <span>{profile.model}</span>
                  )}
                  {profile.gateway_running && (
                    <span className="text-green-500">● Gateway</span>
                  )}
                  {profile.skill_count > 0 && (
                    <span>{profile.skill_count} skills</span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-1 ml-2 flex-shrink-0">
                {!profile.is_active && (
                  <button
                    className="cron-btn run text-xs px-2 py-1"
                    onClick={() => handleSwitch(profile.name)}
                  >
                    {t('profiles.switch') || 'Switch'}
                  </button>
                )}
                {profile.name !== 'default' && (
                  <button
                    className="icon-btn text-[var(--accent)]"
                    onClick={() => handleDelete(profile.name)}
                    title="Delete"
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <line x1="18" y1="6" x2="6" y2="18" />
                      <line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
