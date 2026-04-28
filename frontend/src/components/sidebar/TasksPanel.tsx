import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cronService } from '@/services/api';
import type { CronJob, CronCreateRequest } from '@/types';

export default function TasksPanel() {
  const { t } = useTranslation('common');
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formError, setFormError] = useState('');

  // Form state
  const [formName, setFormName] = useState('');
  const [formSchedule, setFormSchedule] = useState('');
  const [formPrompt, setFormPrompt] = useState('');
  const [formDeliver, setFormDeliver] = useState<'local' | 'discord' | 'telegram'>('local');
  const [formSkills, setFormSkills] = useState<string[]>([]);

  useEffect(() => {
    loadJobs();
  }, []);

  const loadJobs = async () => {
    try {
      setLoading(true);
      const data = await cronService.list();
      setJobs(data.jobs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load jobs');
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
    setFormSchedule('');
    setFormPrompt('');
    setFormDeliver('local');
    setFormSkills([]);
    setFormError('');
  };

  const handleSubmit = async () => {
    if (!formSchedule.trim() || !formPrompt.trim()) {
      setFormError('Schedule and prompt are required');
      return;
    }

    try {
      const jobData: CronCreateRequest = {
        name: formName.trim() || undefined,
        schedule: formSchedule.trim(),
        prompt: formPrompt.trim(),
        deliver: formDeliver,
        skills: formSkills.length > 0 ? formSkills : undefined,
      };

      await cronService.create(jobData);
      loadJobs();
      toggleForm();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to create job');
    }
  };

  const handleRun = async (jobId: string) => {
    try {
      await cronService.run(jobId);
      loadJobs();
    } catch (err) {
      console.error('Failed to run job:', err);
    }
  };

  const handlePause = async (jobId: string) => {
    try {
      await cronService.pause(jobId);
      loadJobs();
    } catch (err) {
      console.error('Failed to pause job:', err);
    }
  };

  const handleResume = async (jobId: string) => {
    try {
      await cronService.resume(jobId);
      loadJobs();
    } catch (err) {
      console.error('Failed to resume job:', err);
    }
  };

  const handleDelete = async (jobId: string) => {
    if (!confirm(t('tasks.confirm_delete') || 'Delete this job?')) return;
    try {
      await cronService.delete(jobId);
      loadJobs();
    } catch (err) {
      console.error('Failed to delete job:', err);
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
    <div className="tasks-panel">
      {/* Header */}
      <div className="flex items-center justify-between p-2 border-b border-[var(--border)]">
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
          {t('tasks.scheduled_jobs') || 'Scheduled Jobs'}
        </span>
        <button
          className="cron-btn run text-xs px-2 py-1"
          onClick={toggleForm}
        >
          + {t('tasks.new_job') || 'New Job'}
        </button>
      </div>

      {/* Create Form */}
      {showForm && (
        <div className="cron-form p-2 border-b border-[var(--border)]">
          <input
            type="text"
            placeholder="Job name (optional)"
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
          />
          <input
            type="text"
            placeholder="Schedule: '0 9 * * *' or 'every 1h'"
            value={formSchedule}
            onChange={(e) => setFormSchedule(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
          />
          <textarea
            rows={3}
            placeholder="Prompt (must be self-contained)"
            value={formPrompt}
            onChange={(e) => setFormPrompt(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded resize-none"
          />
          <select
            value={formDeliver}
            onChange={(e) => setFormDeliver(e.target.value as any)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
          >
            <option value="local">Local (save output only)</option>
            <option value="discord">Discord</option>
            <option value="telegram">Telegram</option>
          </select>
          <div className="flex gap-2">
            <button
              className="cron-btn run flex-1 text-xs py-1"
              onClick={handleSubmit}
            >
              {t('tasks.create_job') || 'Create Job'}
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

      {/* Job List */}
      <div className="cron-list flex-1 overflow-y-auto">
        {error && (
          <div className="p-2 text-sm text-[var(--accent)]">{error}</div>
        )}
        {jobs.length === 0 && !error && (
          <div className="p-3 text-sm text-[var(--muted)] text-center italic">
            {t('tasks.empty') || 'No scheduled jobs'}
          </div>
        )}
        {jobs.map((job) => (
          <div
            key={job.id}
            className={`cron-item p-2 border-b border-[var(--border2)] ${!job.enabled ? 'opacity-50' : ''}`}
          >
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="cron-name text-sm font-medium truncate">
                    {job.name || job.prompt.slice(0, 30) + '...'}
                  </span>
                  {!job.enabled && (
                    <span className="text-xs text-[var(--muted)]">(paused)</span>
                  )}
                </div>
                <div className="text-xs text-[var(--muted)] mt-1">
                  <span className="cron-schedule">{job.schedule}</span>
                </div>
                {job.last_run && (
                  <div className="text-xs text-[var(--muted)] mt-0.5">
                    Last run: {new Date(job.last_run * 1000).toLocaleString()}
                  </div>
                )}
                {job.error && (
                  <div className="text-xs text-[var(--accent)] mt-0.5">
                    Error: {job.error}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-1 ml-2 flex-shrink-0">
                {job.enabled ? (
                  <button
                    className="icon-btn"
                    onClick={() => handlePause(job.id)}
                    title="Pause"
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <rect x="6" y="4" width="4" height="16" />
                      <rect x="14" y="4" width="4" height="16" />
                    </svg>
                  </button>
                ) : (
                  <button
                    className="icon-btn"
                    onClick={() => handleResume(job.id)}
                    title="Resume"
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polygon points="5 3 19 12 5 21 5 3" />
                    </svg>
                  </button>
                )}
                <button
                  className="icon-btn"
                  onClick={() => handleRun(job.id)}
                  title="Run now"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polygon points="5 3 19 12 5 21 5 3" />
                  </svg>
                </button>
                <button
                  className="icon-btn text-[var(--accent)]"
                  onClick={() => handleDelete(job.id)}
                  title="Delete"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
