import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useCronStore } from '@/stores/useCronStore';
import { cronService } from '@/services/api';
import type { CronSchedule } from '@/types';
import { renderMarkdown } from '@/utils/markdown';
import { ChevronRightIcon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';

function parseTimestamp(val: string | number | null | undefined): Date | null {
  if (!val) return null;
  if (typeof val === 'number') return new Date(val * 1000);
  // ISO string
  try {
    const d = new Date(val);
    if (!isNaN(d.getTime())) return d;
  } catch {
    // ignore
  }
  return null;
}

function formatDate(val: string | number | null | undefined): string {
  const d = parseTimestamp(val);
  return d ? d.toLocaleString() : '—';
}

type ScheduleKind = 'interval' | 'cron' | 'unknown';

function getScheduleDisplay(schedule: string | CronSchedule | null | undefined): { kind: ScheduleKind; display: string } {
  if (!schedule) return { kind: 'unknown', display: '—' };
  if (typeof schedule === 'object') {
    const k: ScheduleKind =
      schedule.kind === 'interval' ? 'interval' :
      schedule.kind === 'cron' ? 'cron' : 'unknown';
    return { kind: k, display: schedule.display || JSON.stringify(schedule) };
  }
  // Try to detect if it's a cron expression or interval
  if (schedule.startsWith('every ') || /^\d/.test(schedule)) {
    return { kind: 'interval', display: schedule };
  }
  return { kind: 'cron', display: schedule };
}

// Extract Response / Error content from raw cron output.
// System fields (Job ID, Run Time, Schedule) are stripped — they repeat across
// every record and are already visible in the job detail panel above.
function extractCronResponse(raw: string): { response: string; error: string | null } {
  const sections = raw.split(/\n## /);
  let response = '';
  let error: string | null = null;

  for (let i = 1; i < sections.length; i++) {
    const s = sections[i];
    if (s.startsWith('Response')) {
      response = s.replace(/^Response\n?/, '').trim();
    } else if (s.startsWith('Error')) {
      error = s.replace(/^Error\n?/, '').trim();
    }
  }

  return { response, error };
}

// Parse run timestamp from filename like "2026-07-28_11-20-38.md" or "20260728_112038.md"
function parseFilenameTimestamp(filename: string): string {
  // New format: YYYY-MM-DD_HH-MM-SS
  const newMatch = filename.match(/(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})/);
  if (newMatch) {
    const [, y, mo, d, h, mi, s] = newMatch;
    return `${y}-${mo}-${d} ${h}:${mi}:${s}`;
  }
  // Legacy format: YYYYMMDD_HHMMSS
  const legacyMatch = filename.match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
  if (legacyMatch) {
    const [, y, mo, d, h, mi, s] = legacyMatch;
    return `${y}-${mo}-${d} ${h}:${mi}:${s}`;
  }
  // Unix timestamp prefix
  const tsMatch = filename.match(/^(\d{10,13})/);
  if (tsMatch) {
    const ts = parseInt(tsMatch[1]);
    const d = new Date(ts > 1e12 ? ts : ts * 1000);
    if (!isNaN(d.getTime())) return d.toLocaleString();
  }
  return filename.replace('.md', '');
}

export default function TaskDetail() {
  const { t } = useTranslation('common');
  const selectedJob = useCronStore((s) => s.selectedJob);
  const allJobs = useCronStore((s) => s.jobs);
  const outputs = useCronStore((s) => s.outputs);
  const loadingOutputs = useCronStore((s) => s.loadingOutputs);
  const loadOutputs = useCronStore((s) => s.loadOutputs);
  const refreshJobs = useCronStore((s) => s.refreshJobs);
  const selectJob = useCronStore((s) => s.selectJob);
  const setShowEditJobForm = useCronStore((s) => s.setShowEditJobForm);
  const setJobs = useCronStore((s) => s.setJobs);

  const [expandedOutputs, setExpandedOutputs] = useState<Set<string>>(new Set());
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  useEffect(() => {
    if (selectedJob) {
      setExpandedOutputs(new Set());
      loadOutputs(selectedJob.id);
    }
  }, [selectedJob?.id]);

  // Poll every 30s to auto-refresh job status and execution history
  useEffect(() => {
    if (!selectedJob) return;
    const timer = setInterval(() => {
      refreshJobs();
    }, 30000);
    return () => clearInterval(timer);
  }, [selectedJob?.id]);

  if (!selectedJob) return null;

  const scheduleInfo = getScheduleDisplay(selectedJob.schedule);

  const KIND_LABEL: Record<ScheduleKind, string> = {
    interval: t('tasks.schedule_intervals', 'Intervals'),
    cron: t('tasks.schedule_cron', 'Cron'),
    unknown: '—',
  };

  const renderLastStatus = (s: string | null | undefined): string => {
    if (s === 'ok') return t('tasks.status_ok', 'OK');
    if (s === 'error') return t('tasks.status_error', 'Failed');
    return s ?? '';
  };

  const toggleOutput = (filename: string) => {
    setExpandedOutputs((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) {
        next.delete(filename);
      } else {
        next.add(filename);
      }
      return next;
    });
  };

  const reloadJobs = async () => {
    const data = await cronService.list();
    setJobs(data.jobs);
  };

  const handlePause = async () => {
    setActionLoading('pause');
    try {
      await cronService.pause(selectedJob.id);
      await reloadJobs();
    } catch (err) {
      console.error('Failed to pause job:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleResume = async () => {
    setActionLoading('resume');
    try {
      await cronService.resume(selectedJob.id);
      await reloadJobs();
    } catch (err) {
      console.error('Failed to resume job:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleRun = async () => {
    setActionLoading('run');
    try {
      await cronService.run(selectedJob.id);
      await reloadJobs();
      loadOutputs(selectedJob.id);
    } catch (err) {
      console.error('Failed to run job:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async () => {
    if (!confirm(t('tasks.confirm_delete', 'Delete this job?'))) return;
    setActionLoading('delete');
    try {
      await cronService.delete(selectedJob.id);
      selectJob(null);
      await reloadJobs();
    } catch (err) {
      console.error('Failed to delete job:', err);
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="task-detail flex flex-col overflow-hidden h-full">
      <div className="topbar">
        <div className="flex-1 min-w-0 overflow-hidden">
          <div className="topbar-title">
            {selectedJob.name || selectedJob.prompt.slice(0, 40) + (selectedJob.prompt.length > 40 ? '...' : '')}
          </div>
          <div className="topbar-meta">{t('tasks.job_id', 'ID: ')}{selectedJob.id}</div>
        </div>

        <div className="topbar-chips">
          {/* Pause / Resume */}
          {selectedJob.enabled ? (
            <button
              className="cron-btn text-sm"
              onClick={handlePause}
              disabled={!!actionLoading}
              title={t('tasks.pause', 'Pause')}
            >
              {t('tasks.pause', 'Pause')}
            </button>
          ) : (
            <button
              className="cron-btn text-sm"
              onClick={handleResume}
              disabled={!!actionLoading}
              title={t('tasks.resume', 'Resume')}
            >
              {t('tasks.resume', 'Resume')}
            </button>
          )}

          {/* Run Now */}
          <button
            className="cron-btn text-sm"
            onClick={handleRun}
            disabled={!!actionLoading}
            title={t('tasks.run_now', 'Run now')}
          >
            {t('tasks.run_now', 'Run now')}
          </button>

          {/* Edit */}
          <button
            className="cron-btn text-sm"
            onClick={() => setShowEditJobForm(true)}
            disabled={!!actionLoading}
            title={t('edit', 'Edit')}
          >
            {t('edit', 'Edit')}
          </button>

          {/* Delete */}
          <button
            className="cron-btn danger text-sm"
            onClick={handleDelete}
            disabled={!!actionLoading}
            title={t('tasks.delete', 'Delete')}
          >
            {t('tasks.delete', 'Delete')}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
      <div className="max-w-2xl mx-auto">

      {/* Info Grid */}
      <div className="grid gap-3 mb-5">

        {/* Schedule */}
        <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
          <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('tasks.schedule', 'Schedule')}</div>
          <div className="text-base text-[var(--fg)]">{scheduleInfo.display}</div>
          <div className="text-sm text-[var(--muted)] mt-0.5">{KIND_LABEL[scheduleInfo.kind]}</div>
        </div>

        {/* Last run */}
        <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
          <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('tasks.last_run', 'Last run')}</div>
          <div className="text-base text-[var(--fg)]">
            {formatDate(selectedJob.last_run_at || selectedJob.last_run)}
          </div>
          {selectedJob.last_status && (
            <div className={`text-sm mt-0.5 ${selectedJob.last_status === 'error' ? 'text-[var(--accent)]' : 'text-[var(--muted)]'}`}>
              {t('tasks.last_status_label', 'Status: ')}{renderLastStatus(selectedJob.last_status)}
            </div>
          )}
        </div>

        {/* Next run */}
        <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
          <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('tasks.next_run', 'Next run')}</div>
          <div className="text-base text-[var(--fg)]">
            {selectedJob.next_run_at ? formatDate(selectedJob.next_run_at) : (selectedJob.enabled ? t('tasks.next_pending', 'Pending') : t('tasks.next_paused', 'Paused'))}
          </div>
        </div>

        {/* Deliver / output channel */}
        <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
          <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('tasks.output_channel', 'Output Channel')}</div>
          <div className="text-base text-[var(--fg)] capitalize">
            {selectedJob.deliver === 'local' ? t('tasks.deliver_local', 'Local (save output only)') : selectedJob.deliver}
          </div>
        </div>

        {/* Prompt */}
        <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
          <div className="text-base font-medium text-[var(--muted)] mb-1.5">{t('tasks.prompt', 'Prompt')}</div>
          <pre className="text-base text-[var(--fg)] whitespace-pre-wrap break-words font-sans leading-relaxed">
            {selectedJob.prompt}
          </pre>
        </div>

        {/* Model */}
        {selectedJob.model && (
          <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
            <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('tasks.model', 'Model')}</div>
            <div className="text-base text-[var(--fg)]">{selectedJob.model}</div>
          </div>
        )}

        {/* Workspace */}
        {selectedJob.workdir && (
          <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
            <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('tasks.workspace', 'Workspace')}</div>
            <div className="text-base text-[var(--fg)] font-mono text-sm">{selectedJob.workdir}</div>
          </div>
        )}

        {/* Skills */}
        {selectedJob.skills && selectedJob.skills.length > 0 && (
          <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
            <div className="text-base font-medium text-[var(--muted)] mb-1.5">{t('tasks.skills', 'Skills')}</div>
            <div className="flex flex-wrap gap-1.5">
              {selectedJob.skills.map((s) => (
                <span key={s} className="px-2 py-0.5 text-sm rounded-full bg-[var(--hover-bg)] text-[var(--text)] border border-[var(--border)]">{s}</span>
              ))}
            </div>
          </div>
        )}

        {/* Script block removed — webui no longer exposes script/no_agent; SDK still accepts them for CLI users */}

        {/* Context From */}
        {selectedJob.context_from && selectedJob.context_from.length > 0 && (
          <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
            <div className="text-base font-medium text-[var(--muted)] mb-1.5">{t('tasks.context_from', 'Context From')}</div>
            <div className="flex flex-wrap gap-1.5">
              {selectedJob.context_from.map((id) => {
                const label = id === 'self'
                  ? t('tasks.context_self', 'Self')
                  : allJobs.find((j) => j.id === id)?.name || id;
                return (
                  <span key={id} className="px-2 py-0.5 text-sm rounded-full bg-[var(--hover-bg)] text-[var(--text)] border border-[var(--border)]">
                    {label}
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* Enabled Toolsets */}
        {selectedJob.enabled_toolsets && selectedJob.enabled_toolsets.length > 0 && (
          <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
            <div className="text-base font-medium text-[var(--muted)] mb-1.5">{t('tasks.enabled_toolsets', 'Enabled Toolsets')}</div>
            <div className="flex flex-wrap gap-1.5">
              {selectedJob.enabled_toolsets.map((ts) => (
                <span key={ts} className="px-2 py-0.5 text-sm rounded-full bg-[var(--hover-bg)] text-[var(--text)] border border-[var(--border)]">{ts}</span>
              ))}
            </div>
          </div>
        )}

        {/* Repeat */}
        {selectedJob.repeat && selectedJob.repeat.times != null && (
          <div className="p-4 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-xl">
            <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('tasks.repeat', 'Repeat')}</div>
            <div className="text-base text-[var(--fg)]">
              {selectedJob.repeat.times === 0 ? t('tasks.repeat_forever', 'Forever') : `${selectedJob.repeat.completed} / ${selectedJob.repeat.times}`}
            </div>
          </div>
        )}

      </div>

      {/* Execution History */}
      <div>
        <div className="text-base font-medium text-[var(--muted)] mb-2">
          {t('tasks.execution_history', 'Execution History')}
        </div>

        {loadingOutputs && outputs.length === 0 && (
          <LoadingSpinner mode="inline" />
        )}

        {!loadingOutputs && outputs.length === 0 && (
          <div className="text-sm text-[var(--muted)] text-center py-4 italic">{t('tasks.no_history', 'No execution records')}</div>
        )}

        {outputs.map((output) => {
          const isExpanded = expandedOutputs.has(output.filename);
          const timeStr = parseFilenameTimestamp(output.filename);

          return (
            <div
              key={output.filename}
              className="mb-2 border border-[var(--border2)] rounded-xl overflow-hidden"
            >
              <button
                className="w-full flex items-center justify-between px-3 py-2 bg-[rgba(255,255,255,0.03)] hover:bg-[rgba(255,255,255,0.06)] text-left transition-colors"
                onClick={() => toggleOutput(output.filename)}
              >
                <span className="text-base text-[var(--fg)]">{timeStr}</span>
                <ChevronRightIcon
                  size={18}
                  className={`text-[var(--muted)] transition-transform duration-200 ${isExpanded ? 'rotate-90' : ''}`}
                />
              </button>

              <div
                style={{
                  display: 'grid',
                  gridTemplateRows: isExpanded ? '1fr' : '0fr',
                  transition: 'grid-template-rows 0.2s ease',
                }}
              >
                <div style={{ overflow: 'hidden' }}>
                  {(() => {
                    const { response, error } = extractCronResponse(output.content);
                    if (!response && !error) return null;
                    return (
                      <div className="border-t border-[var(--border2)] bg-[rgba(0,0,0,0.15)]">
                        <div className="pb-4 text-base text-[var(--fg)]">
                          {/* Response label — muted, distinct from AI content */}
                          {response && (
                            <>
                              <div className="px-4 pt-3 pb-2 mb-2 border-b border-[var(--border2)] text-[var(--muted)] text-base font-medium">{t('tasks.response_section', 'Response')}</div>
                              <div className="px-4" dangerouslySetInnerHTML={{ __html: renderMarkdown(response) }} />
                            </>
                          )}
                          {/* Error section */}
                          {error && (
                            <>
                              <div className="px-4 pt-3 pb-2 text-[var(--accent)] text-base font-medium">{t('tasks.error_section', 'Error')}</div>
                              <div className="px-4" dangerouslySetInnerHTML={{ __html: renderMarkdown(error) }} />
                            </>
                          )}
                        </div>
                      </div>
                    );
                  })()}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      </div>
      </div>
    </div>
  );
}
