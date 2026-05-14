import { useEffect, useState } from 'react';
import { useCronStore } from '@/stores/useCronStore';
import { cronService } from '@/services/api';
import type { CronSchedule } from '@/types';
import { renderMarkdown } from '@/utils/markdown';

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

function getScheduleDisplay(schedule: string | CronSchedule | null | undefined): { kind: string; display: string } {
  if (!schedule) return { kind: '—', display: '—' };
  if (typeof schedule === 'object') {
    const kind = schedule.kind === 'interval' ? '固定间隔' : schedule.kind === 'cron' ? 'Cron 表达式' : schedule.kind;
    return { kind, display: schedule.display || JSON.stringify(schedule) };
  }
  // Try to detect if it's a cron expression or interval
  if (schedule.startsWith('every ') || /^\d/.test(schedule)) {
    return { kind: '固定间隔', display: schedule };
  }
  return { kind: 'Cron 表达式', display: schedule };
}

// Parse run timestamp from filename like "20240101_120000.md" or "1700000000.md"
function parseFilenameTimestamp(filename: string): string {
  // Try pattern: YYYYMMDD_HHMMSS
  const isoMatch = filename.match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
  if (isoMatch) {
    const [, y, mo, d, h, mi, s] = isoMatch;
    const dt = new Date(`${y}-${mo}-${d}T${h}:${mi}:${s}`);
    if (!isNaN(dt.getTime())) return dt.toLocaleString();
  }
  // Try unix timestamp
  const tsMatch = filename.match(/^(\d{10,13})/);
  if (tsMatch) {
    const ts = parseInt(tsMatch[1]);
    const d = new Date(ts > 1e12 ? ts : ts * 1000);
    if (!isNaN(d.getTime())) return d.toLocaleString();
  }
  return filename.replace('.md', '');
}

export default function TaskDetail() {
  const selectedJob = useCronStore((s) => s.selectedJob);
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
    if (!confirm('Delete this job?')) return;
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
    <div className="task-detail p-4 overflow-y-auto h-full">
      {/* Title + Action Bar */}
      <div className="flex items-start justify-between mb-4 gap-4">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-[var(--fg)] leading-snug">
            {selectedJob.name || selectedJob.prompt.slice(0, 40) + (selectedJob.prompt.length > 40 ? '...' : '')}
          </h2>
          <div className="text-xs text-[var(--muted)] mt-1">ID: {selectedJob.id}</div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {/* Pause / Resume */}
          {selectedJob.enabled ? (
            <button
              className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md bg-[rgba(255,255,255,0.06)] hover:bg-[rgba(255,255,255,0.1)] text-[var(--fg)] transition-colors disabled:opacity-50"
              onClick={handlePause}
              disabled={!!actionLoading}
              title="Pause"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="6" y="4" width="4" height="16" />
                <rect x="14" y="4" width="4" height="16" />
              </svg>
              Pause
            </button>
          ) : (
            <button
              className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md bg-[rgba(255,255,255,0.06)] hover:bg-[rgba(255,255,255,0.1)] text-[var(--fg)] transition-colors disabled:opacity-50"
              onClick={handleResume}
              disabled={!!actionLoading}
              title="Resume"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M2.5 2v6h6M21.5 22v-6h-6" />
                <path d="M22 11.5A10 10 0 0 0 3.2 7.2M2 12.5a10 10 0 0 0 18.8 4.2" />
              </svg>
              Resume
            </button>
          )}

          {/* Run Now */}
          <button
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md bg-[rgba(255,255,255,0.06)] hover:bg-[rgba(255,255,255,0.1)] text-[var(--fg)] transition-colors disabled:opacity-50"
            onClick={handleRun}
            disabled={!!actionLoading}
            title="Run now"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
            Run Now
          </button>

          {/* Edit */}
          <button
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md bg-[rgba(255,255,255,0.06)] hover:bg-[rgba(255,255,255,0.1)] text-[var(--fg)] transition-colors disabled:opacity-50"
            onClick={() => setShowEditJobForm(true)}
            disabled={!!actionLoading}
            title="Edit"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
            </svg>
            Edit
          </button>

          {/* Delete */}
          <button
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs rounded-md bg-[rgba(255,0,0,0.08)] hover:bg-[rgba(255,0,0,0.15)] text-[var(--accent)] transition-colors disabled:opacity-50"
            onClick={handleDelete}
            disabled={!!actionLoading}
            title="Delete"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
              <path d="M10 11v6M14 11v6" />
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
            </svg>
            Delete
          </button>
        </div>
      </div>

      {/* Info Grid */}
      <div className="grid gap-3 mb-5">

        {/* Schedule */}
        <div className="p-3 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-lg">
          <div className="text-xs text-[var(--muted)] mb-0.5">定时计划</div>
          <div className="text-sm font-medium text-[var(--fg)]">{scheduleInfo.display}</div>
          <div className="text-xs text-[var(--muted)] mt-0.5">{scheduleInfo.kind}</div>
        </div>

        {/* Last run */}
        <div className="p-3 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-lg">
          <div className="text-xs text-[var(--muted)] mb-0.5">上次执行时间</div>
          <div className="text-sm font-medium text-[var(--fg)]">
            {formatDate(selectedJob.last_run_at || selectedJob.last_run)}
          </div>
          {selectedJob.last_status && (
            <div className={`text-xs mt-0.5 ${selectedJob.last_status === 'error' ? 'text-[var(--accent)]' : 'text-[var(--muted)]'}`}>
              状态: {selectedJob.last_status}
            </div>
          )}
        </div>

        {/* Next run */}
        <div className="p-3 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-lg">
          <div className="text-xs text-[var(--muted)] mb-0.5">下次预计执行时间</div>
          <div className="text-sm font-medium text-[var(--fg)]">
            {selectedJob.next_run_at ? formatDate(selectedJob.next_run_at) : (selectedJob.enabled ? '待计算' : '已暂停')}
          </div>
        </div>

        {/* Deliver / output channel */}
        <div className="p-3 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-lg">
          <div className="text-xs text-[var(--muted)] mb-0.5">输出通道</div>
          <div className="text-sm font-medium text-[var(--fg)] capitalize">
            {selectedJob.deliver === 'local' ? '本地 (仅保存输出)' : selectedJob.deliver}
          </div>
        </div>

        {/* Prompt */}
        <div className="p-3 bg-[rgba(255,255,255,0.03)] border border-[var(--border2)] rounded-lg">
          <div className="text-xs text-[var(--muted)] mb-1.5">Prompt</div>
          <pre className="text-sm text-[var(--fg)] whitespace-pre-wrap break-words font-sans leading-relaxed">
            {selectedJob.prompt}
          </pre>
        </div>

      </div>

      {/* Execution History */}
      <div>
        <div className="text-xs font-bold uppercase tracking-wider text-[var(--muted)] mb-2">
          历次执行记录
        </div>

        {loadingOutputs && outputs.length === 0 && (
          <div className="text-sm text-[var(--muted)] text-center py-4">加载中...</div>
        )}

        {!loadingOutputs && outputs.length === 0 && (
          <div className="text-sm text-[var(--muted)] text-center py-4 italic">暂无执行记录</div>
        )}

        {outputs.map((output) => {
          const isExpanded = expandedOutputs.has(output.filename);
          const timeStr = parseFilenameTimestamp(output.filename);

          return (
            <div
              key={output.filename}
              className="mb-2 border border-[var(--border2)] rounded-lg overflow-hidden"
            >
              <button
                className="w-full flex items-center justify-between px-3 py-2 bg-[rgba(255,255,255,0.03)] hover:bg-[rgba(255,255,255,0.06)] text-left transition-colors"
                onClick={() => toggleOutput(output.filename)}
              >
                <span className="text-sm text-[var(--fg)]">{timeStr}</span>
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  className={`text-[var(--muted)] transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`}
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </button>

              <div
                style={{
                  display: 'grid',
                  gridTemplateRows: isExpanded ? '1fr' : '0fr',
                  transition: 'grid-template-rows 0.2s ease',
                }}
              >
                <div style={{ overflow: 'hidden' }}>
                  <div
                    className="p-3 text-sm text-[var(--fg)] border-t border-[var(--border2)] bg-[rgba(0,0,0,0.15)] prose prose-sm max-w-none"
                    style={{ color: 'var(--fg)' }}
                    dangerouslySetInnerHTML={{ __html: renderMarkdown(output.content) }}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
