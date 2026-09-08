import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { cronService, modelsService, skillsService, toolsetsService } from '@/services/api';
import type { CronCreateRequest, CronJob, ModelGroup } from '@/types';
import { useCronStore } from '@/stores/useCronStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import FormPage, { FormActions } from '@/components/common/FormPage';
import { FormField, TextField, TextArea } from '@/components/common/FormField';
import { ChevronDownIcon } from '@/components/common/Icons';
import CustomSelect from '@/components/common/CustomSelect';

interface TaskFormProps {
  editJob?: CronJob;
  onSuccess: () => void;
  onCancel: () => void;
}

export default function TaskForm({ editJob, onSuccess, onCancel }: TaskFormProps) {
  const { t } = useTranslation('common');
  const setStoreJobs = useCronStore((s) => s.setJobs);
  const jobs = useCronStore((s) => s.jobs);

  const getScheduleString = (job?: CronJob): string => {
    if (!job) return '';
    if (typeof job.schedule === 'string') return job.schedule;
    if (typeof job.schedule === 'object' && job.schedule !== null) {
      return job.schedule.display || '';
    }
    return '';
  };

  // ── Tier 1: Basic fields ──
  const [formName, setFormName] = useState(editJob?.name || '');
  const [formSchedule, setFormSchedule] = useState(getScheduleString(editJob));
  const [formPrompt, setFormPrompt] = useState(editJob?.prompt || '');
  const [formDeliver, setFormDeliver] = useState<'local' | 'discord' | 'telegram'>(editJob?.deliver || 'local');
  const defaultModel = useSettingsStore((s) => s.settings?.model ?? '');
  const [modelGroups, setModelGroups] = useState<ModelGroup[]>([]);
  const [formModel, setFormModel] = useState(editJob?.model || defaultModel || '');
  const [formError, setFormError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // ── Tier 2: Workspace + Skills ──
  const [formWorkdir, setFormWorkdir] = useState(editJob?.workdir || '');
  const [formSkills, setFormSkills] = useState<string[]>(editJob?.skills || []);
  const [availableSkills, setAvailableSkills] = useState<{ name: string; label: string }[]>([]);

  // Registered workspaces for the Workspace dropdown
  const workspaces = useWorkspaceStore((s) => s.workspaces);
  const workspacesLoaded = useWorkspaceStore((s) => s.workspacesLoaded);
  const loadWorkspaces = useWorkspaceStore((s) => s.loadWorkspaces);

  // ── Tier 3: Advanced options ──
  const [formContextFrom, setFormContextFrom] = useState<string[]>(editJob?.context_from || []);
  const [formEnabledToolsets, setFormEnabledToolsets] = useState<string[]>(editJob?.enabled_toolsets || []);
  const [formRepeat, setFormRepeat] = useState<string>(
    editJob?.repeat?.times != null ? String(editJob.repeat.times) : ''
  );
  const [availableToolsets, setAvailableToolsets] = useState<string[]>([]);

  // Context From dropdown open state
  const [ctxDropdownOpen, setCtxDropdownOpen] = useState(false);
  const ctxDropdownRef = useRef<HTMLDivElement>(null);
  // Toolsets dropdown open state
  const [tsDropdownOpen, setTsDropdownOpen] = useState(false);
  const tsDropdownRef = useRef<HTMLDivElement>(null);
  // Skills dropdown open state
  const [skillsDropdownOpen, setSkillsDropdownOpen] = useState(false);
  const skillsDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    modelsService.getModels()
      .then((resp) => setModelGroups(resp.groups ?? []))
      .catch(() => {});
    // Load skills
    skillsService.list()
      .then((resp) => setAvailableSkills(resp.skills?.map(s => ({ name: s.name, label: s.label || s.name })) ?? []))
      .catch(() => {});
    // Load toolsets
    toolsetsService.list()
      .then((resp) => {
        const ts = Object.keys(resp.toolsets ?? {});
        setAvailableToolsets(ts);
        // New jobs default to all toolsets enabled (matches SDK's "no filter = all"
        // semantic). Edit mode preserves the job's explicit selection.
        if (!editJob?.enabled_toolsets) {
          setFormEnabledToolsets(ts);
        }
      })
      .catch(() => {});
    // Load registered workspaces
    if (!workspacesLoaded) {
      loadWorkspaces().catch(() => {});
    }
  }, []);

  // Close dropdowns on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (ctxDropdownRef.current && !ctxDropdownRef.current.contains(e.target as Node)) {
        setCtxDropdownOpen(false);
      }
      if (tsDropdownRef.current && !tsDropdownRef.current.contains(e.target as Node)) {
        setTsDropdownOpen(false);
      }
      if (skillsDropdownRef.current && !skillsDropdownRef.current.contains(e.target as Node)) {
        setSkillsDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const isEdit = !!editJob;

  // ── Toggle helpers for multi-select dropdowns ──
  const toggleSkill = (name: string) => {
    setFormSkills((prev) =>
      prev.includes(name) ? prev.filter((s) => s !== name) : [...prev, name]
    );
  };

  const toggleContextFrom = (jobId: string) => {
    setFormContextFrom((prev) =>
      prev.includes(jobId) ? prev.filter((id) => id !== jobId) : [...prev, jobId]
    );
  };

  const toggleToolset = (name: string) => {
    setFormEnabledToolsets((prev) =>
      prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name]
    );
  };

  // ── Submit ──
  const handleSubmit = async () => {
    if (!formSchedule.trim() || !formPrompt.trim() || !formModel) {
      setFormError(t('tasks.error_required', 'Schedule, prompt, and model are required'));
      return;
    }

    setSubmitting(true);
    try {
      const jobData: CronCreateRequest = {
        name: formName.trim() || undefined,
        schedule: formSchedule.trim(),
        prompt: formPrompt.trim(),
        model: formModel,
        deliver: formDeliver,
        // Tier 2
        workdir: formWorkdir.trim() || undefined,
        skills: formSkills.length > 0 ? formSkills : undefined,
        // Tier 3
        context_from: formContextFrom.length > 0 ? formContextFrom : undefined,
        // "All toolsets checked" is semantically equivalent to undefined (SDK default),
        // so send undefined to keep the job's stored state explicit/clean.
        enabled_toolsets:
          formEnabledToolsets.length === availableToolsets.length && availableToolsets.length > 0
            ? undefined
            : formEnabledToolsets.length > 0
              ? formEnabledToolsets
              : undefined,
        repeat: formRepeat ? parseInt(formRepeat, 10) : undefined,
      };

      if (isEdit) {
        await cronService.update(editJob.id, { ...jobData } as Record<string, unknown>);
      } else {
        await cronService.create(jobData);
      }

      const data = await cronService.list();
      setStoreJobs(data.jobs);
      onSuccess();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : `Failed to ${isEdit ? 'update' : 'create'} job`);
    } finally {
      setSubmitting(false);
    }
  };

  // Filter out self and already-selected items for Context From
  const otherJobs = jobs.filter((j) => j.id !== editJob?.id);

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
      {/* ── Tier 1: Basic fields ── */}
      <FormField label={t('tasks.job_name', 'Job Name')} hint={t('tasks.job_name_hint', '(optional)')}>
        <TextField
          type="text"
          placeholder={t('tasks.job_name_placeholder', 'My scheduled task')}
          value={formName}
          onChange={(e) => setFormName(e.target.value)}
        />
      </FormField>

      <FormField
        label={t('tasks.schedule', 'Schedule')}
        required
        helper={
          <>
            <p>{t('tasks.schedule_intervals', 'Intervals')}: <code>every 10m</code> <code>every 2h</code> <code>every 1d</code></p>
            <p>{t('tasks.schedule_cron', 'Cron')}: <code>0 9 * * *</code> <code>*/30 * * * *</code></p>
            <p>{t('tasks.schedule_oneshot', 'One-shot')}: <code>2026-08-01T09:00</code></p>
          </>
        }
      >
        <TextField
          type="text"
          placeholder={t('tasks.schedule_placeholder', "'0 9 * * *' or 'every 1h'")}
          value={formSchedule}
          onChange={(e) => setFormSchedule(e.target.value)}
        />
      </FormField>

      <FormField label={t('tasks.prompt', 'Prompt')} required>
        <TextArea
          rows={8}
          placeholder={t('tasks.prompt_placeholder', 'Prompt (must be self-contained, no context assumed)')}
          value={formPrompt}
          onChange={(e) => setFormPrompt(e.target.value)}
          style={{ minHeight: 180 }}
        />
      </FormField>

      <FormField label={t('tasks.model', 'Model')} required>
        <CustomSelect
          value={formModel}
          onChange={setFormModel}
          placeholder={t('tasks.select_model', 'Select a model')}
          options={modelGroups.flatMap((group) =>
            group.models.map((m) => ({
              value: m.id,
              label: `${m.label} (${m.id})`,
              description: group.provider,
            }))
          )}
        />
      </FormField>

      <FormField label={t('tasks.output_channel', 'Output Channel')}>
        <CustomSelect
          value={formDeliver}
          onChange={(v) => setFormDeliver(v as 'local' | 'discord' | 'telegram')}
          placeholder={t('tasks.deliver_local', 'Local (save output only)')}
          options={[
            { value: 'local', label: t('tasks.deliver_local', 'Local (save output only)') },
            { value: 'discord', label: 'Discord' },
            { value: 'telegram', label: 'Telegram' },
          ]}
        />
      </FormField>

      {/* Workspace */}
      <FormField
        label={t('tasks.workspace', 'Workspace')}
        hint={t('tasks.workspace_hint', '(optional)')}
        helper={t('tasks.workspace_helper', 'Determines AGENTS.md/CLAUDE.md context and tool working directory.')}
      >
        <CustomSelect
          value={formWorkdir}
          onChange={setFormWorkdir}
          placeholder={t('tasks.workspace_placeholder', 'Select a workspace')}
          options={[
            ...workspaces.map((w) => ({
              value: w.path,
              label: w.display_name || w.name,
              description: w.path,
            })),
          ]}
        />
      </FormField>

      {/* Skills */}
      <FormField
        label={t('tasks.skills', 'Skills')}
        hint={t('tasks.skills_hint', '(optional)')}
        helper={t('tasks.skills_helper', 'Load these skills before running the prompt.')}
      >
        <div className="relative" ref={skillsDropdownRef}>
          <button
            type="button"
            className="form-input text-left flex items-center justify-between gap-2"
            onClick={() => setSkillsDropdownOpen(!skillsDropdownOpen)}
          >
            <span className={formSkills.length > 0 ? 'text-[var(--text)]' : 'text-[var(--muted)]'}>
              {formSkills.length > 0
                ? t('tasks.skills_count', '{{count}} skill(s) selected', { count: formSkills.length })
                : t('tasks.skills_none', 'No skills selected')}
            </span>
            <ChevronDownIcon size={14} className={`transition-transform ${skillsDropdownOpen ? 'rotate-180' : ''}`} />
          </button>
          {/* Selected skills chips */}
          {formSkills.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {formSkills.map((s) => (
                <span
                  key={s}
                  className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-[var(--accent)]/15 text-[var(--accent)] cursor-pointer hover:bg-[var(--accent)]/25"
                  onClick={() => toggleSkill(s)}
                >
                  {s}
                  <span className="text-[10px]">×</span>
                </span>
              ))}
            </div>
          )}
          {skillsDropdownOpen && (
            <div className="absolute left-0 right-0 top-full mt-1 z-50 rounded-lg border border-[var(--border)] bg-[var(--sidebar-bg,#1e1e1e)] shadow-xl overflow-hidden"
              style={{ maxHeight: 240, overflowY: 'auto' }}
            >
              {availableSkills.length === 0 ? (
                <div className="px-3 py-2 text-xs text-[var(--muted)]">{t('tasks.no_skills_available', 'No skills available')}</div>
              ) : (
                availableSkills.map((s) => (
                  <button
                    key={s.name}
                    type="button"
                    className={`w-full text-left px-3 py-1.5 text-xs transition-colors flex items-center gap-2 ${
                      formSkills.includes(s.name)
                        ? 'bg-[var(--accent)]/10 text-[var(--accent)]'
                        : 'text-[var(--muted)] hover:bg-[var(--hover-bg)] hover:text-[var(--text)]'
                    }`}
                    onClick={() => toggleSkill(s.name)}
                  >
                    <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center text-[10px] ${
                      formSkills.includes(s.name)
                        ? 'border-[var(--accent)] bg-[var(--accent)] text-white'
                        : 'border-[var(--border)]'
                    }`}>
                      {formSkills.includes(s.name) && '✓'}
                    </span>
                    {s.label}
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      </FormField>

      {/* Context From */}
      <FormField
        label={t('tasks.context_from', 'Context From')}
        hint={t('tasks.context_from_hint', '(optional)')}
        helper={t('tasks.context_from_helper', 'Inject the latest output from other jobs as context. Works regardless of the source job\'s output channel.')}
      >
        <div className="relative" ref={ctxDropdownRef}>
          <button
            type="button"
            className="form-input text-left flex items-center justify-between gap-2"
            onClick={() => setCtxDropdownOpen(!ctxDropdownOpen)}
          >
            <span className={formContextFrom.length > 0 ? 'text-[var(--text)]' : 'text-[var(--muted)]'}>
              {formContextFrom.length > 0
                ? t('tasks.context_from_count', '{{count}} source(s) selected', { count: formContextFrom.length })
                : t('tasks.context_from_none', 'No context sources')}
            </span>
            <ChevronDownIcon size={14} className={`transition-transform ${ctxDropdownOpen ? 'rotate-180' : ''}`} />
          </button>
          {/* Selected chips */}
          {formContextFrom.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {formContextFrom.map((id) => {
                const label = id === 'self'
                  ? t('tasks.context_self', 'Self')
                  : jobs.find((j) => j.id === id)?.name || id;
                return (
                  <span
                    key={id}
                    className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-[var(--accent)]/15 text-[var(--accent)] cursor-pointer hover:bg-[var(--accent)]/25"
                    onClick={() => toggleContextFrom(id)}
                  >
                    {label}
                    <span className="text-[10px]">×</span>
                  </span>
                );
              })}
            </div>
          )}
          {ctxDropdownOpen && (
            <div className="absolute left-0 right-0 top-full mt-1 z-50 rounded-lg border border-[var(--border)] bg-[var(--sidebar-bg,#1e1e1e)] shadow-xl overflow-hidden"
              style={{ maxHeight: 240, overflowY: 'auto' }}
            >
              {/* Self option */}
              <button
                type="button"
                className={`w-full text-left px-3 py-1.5 text-xs transition-colors flex items-center gap-2 ${
                  formContextFrom.includes('self')
                    ? 'bg-[var(--accent)]/10 text-[var(--accent)]'
                    : 'text-[var(--muted)] hover:bg-[var(--hover-bg)] hover:text-[var(--text)]'
                }`}
                onClick={() => toggleContextFrom('self')}
              >
                <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center text-[10px] ${
                  formContextFrom.includes('self')
                    ? 'border-[var(--accent)] bg-[var(--accent)] text-white'
                    : 'border-[var(--border)]'
                }`}>
                  {formContextFrom.includes('self') && '✓'}
                </span>
                {t('tasks.context_self_option', 'Self — inject previous run\'s output')}
              </button>
              {/* Other jobs */}
              {otherJobs.length === 0 ? (
                <div className="px-3 py-2 text-xs text-[var(--muted)]">{t('tasks.no_other_jobs', 'No other jobs available')}</div>
              ) : (
                otherJobs.map((j) => (
                  <button
                    key={j.id}
                    type="button"
                    className={`w-full text-left px-3 py-1.5 text-xs transition-colors flex items-center gap-2 ${
                      formContextFrom.includes(j.id)
                        ? 'bg-[var(--accent)]/10 text-[var(--accent)]'
                        : 'text-[var(--muted)] hover:bg-[var(--hover-bg)] hover:text-[var(--text)]'
                    }`}
                    onClick={() => toggleContextFrom(j.id)}
                  >
                    <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center text-[10px] ${
                      formContextFrom.includes(j.id)
                        ? 'border-[var(--accent)] bg-[var(--accent)] text-white'
                        : 'border-[var(--border)]'
                    }`}>
                      {formContextFrom.includes(j.id) && '✓'}
                    </span>
                    {j.name || j.prompt.slice(0, 30)} <span className="text-[var(--muted)] opacity-60">({j.id})</span>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      </FormField>

      {/* Enabled Toolsets */}
      <FormField
        label={t('tasks.enabled_toolsets', 'Enabled Toolsets')}
        hint={t('tasks.toolsets_hint', '(optional)')}
        helper={t('tasks.toolsets_helper', 'Only tools from these toolsets are loaded, reducing token overhead.')}
      >
        <div className="relative" ref={tsDropdownRef}>
          <button
            type="button"
            className="form-input text-left flex items-center justify-between gap-2"
            onClick={() => setTsDropdownOpen(!tsDropdownOpen)}
          >
            <span className={
              formEnabledToolsets.length === availableToolsets.length && availableToolsets.length > 0
                ? 'text-[var(--text)]'
                : formEnabledToolsets.length > 0
                  ? 'text-[var(--text)]'
                  : 'text-[var(--muted)]'
            }>
              {formEnabledToolsets.length === availableToolsets.length && availableToolsets.length > 0
                ? t('tasks.toolsets_all', 'All toolsets (default)')
                : formEnabledToolsets.length > 0
                  ? t('tasks.toolsets_count', '{{count}} toolset(s) selected', { count: formEnabledToolsets.length })
                  : t('tasks.toolsets_all', 'All toolsets (default)')}
            </span>
            <ChevronDownIcon size={14} className={`transition-transform ${tsDropdownOpen ? 'rotate-180' : ''}`} />
          </button>
          {/* Selected chips */}
          {formEnabledToolsets.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {formEnabledToolsets.map((ts) => (
                <span
                  key={ts}
                  className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-[var(--accent)]/15 text-[var(--accent)] cursor-pointer hover:bg-[var(--accent)]/25"
                  onClick={() => toggleToolset(ts)}
                >
                  {ts}
                  <span className="text-[10px]">×</span>
                </span>
              ))}
            </div>
          )}
          {tsDropdownOpen && (
            <div className="absolute left-0 right-0 top-full mt-1 z-50 rounded-lg border border-[var(--border)] bg-[var(--sidebar-bg,#1e1e1e)] shadow-xl overflow-hidden"
              style={{ maxHeight: 240, overflowY: 'auto' }}
            >
              {availableToolsets.length === 0 ? (
                <div className="px-3 py-2 text-xs text-[var(--muted)]">{t('tasks.no_toolsets', 'No toolsets available')}</div>
              ) : (
                availableToolsets.map((ts) => (
                  <button
                    key={ts}
                    type="button"
                    className={`w-full text-left px-3 py-1.5 text-xs transition-colors flex items-center gap-2 ${
                      formEnabledToolsets.includes(ts)
                        ? 'bg-[var(--accent)]/10 text-[var(--accent)]'
                        : 'text-[var(--muted)] hover:bg-[var(--hover-bg)] hover:text-[var(--text)]'
                    }`}
                    onClick={() => toggleToolset(ts)}
                  >
                    <span className={`w-3.5 h-3.5 rounded border flex items-center justify-center text-[10px] ${
                      formEnabledToolsets.includes(ts)
                        ? 'border-[var(--accent)] bg-[var(--accent)] text-white'
                        : 'border-[var(--border)]'
                    }`}>
                      {formEnabledToolsets.includes(ts) && '✓'}
                    </span>
                    {ts}
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      </FormField>

      {/* Repeat */}
      <FormField
        label={t('tasks.repeat', 'Repeat')}
        hint={t('tasks.repeat_hint', '(empty = forever, 1 = once)')}
        helper={t('tasks.repeat_helper', 'For one-shot schedules, auto-set to 1. Override here to run multiple times.')}
      >
        <TextField
          type="number"
          placeholder={t('tasks.repeat_placeholder', 'e.g. 5')}
          value={formRepeat}
          onChange={(e) => setFormRepeat(e.target.value)}
          min={0}
        />
      </FormField>
    </FormPage>
  );
}
