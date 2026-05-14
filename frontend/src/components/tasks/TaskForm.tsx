import { useState } from 'react';
import { cronService } from '@/services/api';
import type { CronCreateRequest, CronJob } from '@/types';
import { useCronStore } from '@/stores/useCronStore';

interface TaskFormProps {
  editJob?: CronJob;
  onSuccess: () => void;
  onCancel: () => void;
}

export default function TaskForm({ editJob, onSuccess, onCancel }: TaskFormProps) {
  const setStoreJobs = useCronStore((s) => s.setJobs);

  const getScheduleString = (job?: CronJob): string => {
    if (!job) return '';
    if (typeof job.schedule === 'string') return job.schedule;
    if (typeof job.schedule === 'object' && job.schedule !== null) {
      return job.schedule.display || '';
    }
    return '';
  };

  const [formName, setFormName] = useState(editJob?.name || '');
  const [formSchedule, setFormSchedule] = useState(getScheduleString(editJob));
  const [formPrompt, setFormPrompt] = useState(editJob?.prompt || '');
  const [formDeliver, setFormDeliver] = useState<'local' | 'discord' | 'telegram'>(editJob?.deliver || 'local');
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const isEdit = !!editJob;

  const handleSubmit = async () => {
    if (!formSchedule.trim() || !formPrompt.trim()) {
      setFormError('Schedule and prompt are required');
      return;
    }

    setSubmitting(true);
    try {
      if (isEdit) {
        await cronService.update(editJob.id, {
          name: formName.trim() || undefined,
          schedule: formSchedule.trim(),
          prompt: formPrompt.trim(),
          deliver: formDeliver,
        });
      } else {
        const jobData: CronCreateRequest = {
          name: formName.trim() || undefined,
          schedule: formSchedule.trim(),
          prompt: formPrompt.trim(),
          deliver: formDeliver,
        };
        await cronService.create(jobData);
      }

      // Refresh jobs in store
      const data = await cronService.list();
      setStoreJobs(data.jobs);

      onSuccess();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : `Failed to ${isEdit ? 'update' : 'create'} job`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col flex-1 p-6 overflow-y-auto min-h-0">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-[var(--fg)]">
          {isEdit ? 'Edit Scheduled Job' : 'New Scheduled Job'}
        </h2>
        <button
          className="icon-btn"
          onClick={onCancel}
          title="Cancel"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      <div className="flex flex-col flex-1 gap-4">
        <div>
          <label className="block text-xs text-[var(--muted)] mb-1">Job Name (optional)</label>
          <input
            type="text"
            placeholder="My scheduled task"
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            className="w-full px-3 py-2 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded-lg focus:outline-none focus:border-[var(--accent)]"
          />
        </div>

        <div>
          <label className="block text-xs text-[var(--muted)] mb-1">Schedule <span className="text-[var(--accent)]">*</span></label>
          <input
            type="text"
            placeholder="'0 9 * * *' or 'every 1h'"
            value={formSchedule}
            onChange={(e) => setFormSchedule(e.target.value)}
            className="w-full px-3 py-2 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded-lg focus:outline-none focus:border-[var(--accent)]"
          />
          <div className="text-xs text-[var(--muted)] mt-1">Cron expression or interval like "every 1h", "every 30m"</div>
        </div>

        <div className="flex flex-col flex-1 min-h-0">
          <label className="block text-xs text-[var(--muted)] mb-1">Prompt <span className="text-[var(--accent)]">*</span></label>
          <textarea
            placeholder="Prompt (must be self-contained, no context assumed)"
            value={formPrompt}
            onChange={(e) => setFormPrompt(e.target.value)}
            className="flex-1 w-full px-3 py-2 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded-lg focus:outline-none focus:border-[var(--accent)] resize-none"
          />
        </div>

        <div>
          <label className="block text-xs text-[var(--muted)] mb-1">Output Channel</label>
          <select
            value={formDeliver}
            onChange={(e) => setFormDeliver(e.target.value as 'local' | 'discord' | 'telegram')}
            className="w-full px-3 py-2 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded-lg focus:outline-none focus:border-[var(--accent)]"
          >
            <option value="local">Local (save output only)</option>
            <option value="discord">Discord</option>
            <option value="telegram">Telegram</option>
          </select>
        </div>

        {formError && (
          <div className="text-sm text-[var(--accent)] px-1">{formError}</div>
        )}

        <div className="flex gap-3 pt-2">
          <button
            className="cron-btn run flex-1 py-2 text-sm"
            onClick={handleSubmit}
            disabled={submitting}
          >
            {submitting ? (isEdit ? 'Saving...' : 'Creating...') : (isEdit ? 'Save Changes' : 'Create Job')}
          </button>
          <button
            className="cron-btn flex-1 py-2 text-sm"
            onClick={onCancel}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
