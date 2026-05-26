import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cronService } from '@/services/api';
import { useCronStore } from '@/stores/useCronStore';

export default function TasksPanel() {
  const { t } = useTranslation('common');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Read jobs from store so any update (from TaskDetail actions) is reflected immediately
  const jobs = useCronStore((s) => s.jobs);
  const selectedJob = useCronStore((s) => s.selectedJob);
  const selectJob = useCronStore((s) => s.selectJob);
  const setStoreJobs = useCronStore((s) => s.setJobs);
  const setShowNewJobForm = useCronStore((s) => s.setShowNewJobForm);
  const unreadJobIds = useCronStore((s) => s.unreadJobIds);

  useEffect(() => {
    loadJobs();
  }, []);

  const loadJobs = async () => {
    try {
      setLoading(true);
      const data = await cronService.list();
      setStoreJobs(data.jobs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load jobs');
    } finally {
      setLoading(false);
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
          onClick={() => setShowNewJobForm(true)}
          title="New Job"
        >
          +
        </button>
      </div>

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
            className={`cron-item p-2 border-b border-[var(--border2)] cursor-pointer hover:bg-[rgba(255,255,255,0.04)] ${!job.enabled ? 'opacity-60' : ''} ${selectedJob?.id === job.id ? 'bg-[rgba(255,255,255,0.07)]' : ''}`}
            onClick={() => selectJob(job)}
          >
            <div className="flex items-start gap-2">
              {/* Blinking green dot for enabled jobs */}
              <div className="flex-shrink-0 mt-1.5">
                {job.enabled ? (
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                  </span>
                ) : (
                  <span className="inline-flex rounded-full h-2 w-2 bg-[var(--muted)] opacity-40" />
                )}
              </div>

              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1">
                  <span className="cron-name text-sm font-medium truncate">
                    {job.name || job.prompt.slice(0, 30) + '...'}
                  </span>
                  {unreadJobIds.has(job.id) && selectedJob?.id !== job.id && (
                    <span className="inline-flex rounded-full h-2 w-2 bg-[#e5363b] flex-shrink-0" />
                  )}
                  {!job.enabled && (
                    <span className="text-xs text-[var(--muted)] flex-shrink-0">(paused)</span>
                  )}
                </div>
                <div className="text-xs text-[var(--muted)] mt-0.5">
                  <span className="cron-schedule">
                    {typeof job.schedule === 'object' && job.schedule !== null
                      ? (job.schedule as { display?: string }).display ?? JSON.stringify(job.schedule)
                      : job.schedule}
                  </span>
                </div>
                {job.last_run && (
                  <div className="text-xs text-[var(--muted)] mt-0.5">
                    Last run: {new Date(job.last_run * 1000).toLocaleString()}
                  </div>
                )}
                {job.error && (
                  <div className="text-xs text-[var(--accent)] mt-0.5 truncate">
                    Error: {job.error}
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
