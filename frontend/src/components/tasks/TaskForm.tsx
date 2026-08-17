import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { cronService, modelsService } from '@/services/api';
import type { CronCreateRequest, CronJob, ModelGroup } from '@/types';
import { useCronStore } from '@/stores/useCronStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import FormPage, { FormActions } from '@/components/common/FormPage';
import { FormField, TextField, TextArea, SelectField } from '@/components/common/FormField';

interface TaskFormProps {
  editJob?: CronJob;
  onSuccess: () => void;
  onCancel: () => void;
}

export default function TaskForm({ editJob, onSuccess, onCancel }: TaskFormProps) {
  const { t } = useTranslation('common');
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
  const defaultModel = useSettingsStore((s) => s.settings?.model ?? '');
  const [modelGroups, setModelGroups] = useState<ModelGroup[]>([]);
  const [formModel, setFormModel] = useState(editJob?.model || defaultModel || '');
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    modelsService.getModels()
      .then((resp) => setModelGroups(resp.groups ?? []))
      .catch(() => {});
  }, []);

  const isEdit = !!editJob;

  const handleSubmit = async () => {
    if (!formSchedule.trim() || !formPrompt.trim() || !formModel) {
      setFormError('Schedule, prompt, and model are required');
      return;
    }

    setSubmitting(true);
    try {
      if (isEdit) {
        await cronService.update(editJob.id, {
          name: formName.trim() || undefined,
          schedule: formSchedule.trim(),
          prompt: formPrompt.trim(),
          model: formModel,
          deliver: formDeliver,
        });
      } else {
        const jobData: CronCreateRequest = {
          name: formName.trim() || undefined,
          schedule: formSchedule.trim(),
          prompt: formPrompt.trim(),
          model: formModel,
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
    <FormPage
      title={isEdit ? t('tasks.edit_job') : t('tasks.new_job')}
      error={formError}
      actions={
        <FormActions
          onSubmit={handleSubmit}
          onCancel={onCancel}
          busy={submitting}
          submitLabel={isEdit ? t('common.save') : undefined}
          busyLabel={isEdit ? t('common.saving') : undefined}
        />
      }
    >
      <FormField label="Job Name" hint="(optional)">
        <TextField
          type="text"
          placeholder="My scheduled task"
          value={formName}
          onChange={(e) => setFormName(e.target.value)}
        />
      </FormField>

      <FormField
        label="Schedule"
        required
        helper={
          <>
            <p>Intervals: <code>every 10m</code> <code>every 2h</code> <code>every 1d</code></p>
            <p>Cron: <code>0 9 * * *</code> <code>*/30 * * * *</code></p>
            <p>One-shot: <code>2026-08-01T09:00</code></p>
          </>
        }
      >
        <TextField
          type="text"
          placeholder="'0 9 * * *' or 'every 1h'"
          value={formSchedule}
          onChange={(e) => setFormSchedule(e.target.value)}
        />
      </FormField>

      <FormField label="Prompt" required className="flex flex-col flex-1 min-h-0">
        <TextArea
          placeholder="Prompt (must be self-contained, no context assumed)"
          value={formPrompt}
          onChange={(e) => setFormPrompt(e.target.value)}
          className="flex-1"
        />
      </FormField>

      <FormField label="Model" required>
        <SelectField value={formModel} onChange={(e) => setFormModel(e.target.value)}>
          <option value="">-- Select a model --</option>
          {modelGroups.map((group) => (
            <optgroup key={group.provider} label={group.provider}>
              {group.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} ({m.id})
                </option>
              ))}
            </optgroup>
          ))}
        </SelectField>
      </FormField>

      <FormField label="Output Channel">
        <SelectField
          value={formDeliver}
          onChange={(e) => setFormDeliver(e.target.value as 'local' | 'discord' | 'telegram')}
        >
          <option value="local">Local (save output only)</option>
          <option value="discord">Discord</option>
          <option value="telegram">Telegram</option>
        </SelectField>
      </FormField>
    </FormPage>
  );
}
