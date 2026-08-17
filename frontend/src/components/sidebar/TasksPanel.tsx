import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cronService } from '@/services/api';
import { useCronStore } from '@/stores/useCronStore';
import CustomScrollbar from '@/components/common/CustomScrollbar';
import { Icon } from '@/components/common/Icons';

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
      <div className="sidebar-header">
        <span className="sidebar-header-title">
          {t('tasks.scheduled_jobs') || 'Scheduled Jobs'}
        </span>
        <div className="sidebar-header-actions">
          <button
            className="icon-btn"
            onClick={() => setShowNewJobForm(true)}
            title="New Job"
          >
            <Icon name="add" size={18} />
          </button>
        </div>
      </div>

      {/* Job List */}
      <CustomScrollbar className="flex-1">
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
            className={`sidebar-item${!job.enabled ? ' opacity-[0.38]' : ''}${selectedJob?.id === job.id ? ' active' : ''}`}
            onClick={() => selectJob(job)}
          >
            <div className="sidebar-item-text">
              <div className="sidebar-item-title-row">
                <span className="sidebar-item-title">
                  {job.name || job.prompt.slice(0, 30) + '...'}
                </span>
              </div>
              {job.last_run && (
                <div className="text-sm text-[var(--muted)] mt-0.5">
                  Last run: {new Date(job.last_run * 1000).toLocaleString()}
                </div>
              )}
              {job.error && (
                <div className="text-sm text-[var(--accent)] mt-0.5 truncate">
                  Error: {job.error}
                </div>
              )}
            </div>
            <div className="sidebar-item-actions">
              <span className="sidebar-item-dot-wrap">
                <span className={`sidebar-item-streaming-dot${job.enabled ? '' : ' inactive'}`} />
              </span>
            </div>
          </div>
        ))}
      </CustomScrollbar>
    </div>
  );
}
