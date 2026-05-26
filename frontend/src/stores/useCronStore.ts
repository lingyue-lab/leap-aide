import { create } from 'zustand';
import type { CronJob } from '@/types';
import { cronService } from '@/services/api';
import { useSettingsStore } from '@/stores/useSettingsStore';

interface CronOutput {
  filename: string;
  content: string;
}

interface CronState {
  jobs: CronJob[];
  selectedJob: CronJob | null;
  outputs: CronOutput[];
  loadingOutputs: boolean;
  tasksTabActive: boolean;
  showNewJobForm: boolean;
  showEditJobForm: boolean;
  unreadCronCount: number;
  unreadJobIds: Set<string>;

  setJobs: (jobs: CronJob[]) => void;
  selectJob: (job: CronJob | null) => void;
  loadOutputs: (jobId: string) => Promise<void>;
  silentRefreshOutputs: (jobId: string) => Promise<void>;
  refreshJobs: () => Promise<void>;
  setTasksTabActive: (active: boolean) => void;
  updateSelectedJob: (job: CronJob) => void;
  setShowNewJobForm: (show: boolean) => void;
  setShowEditJobForm: (show: boolean) => void;
  refreshUnreadCount: () => Promise<void>;
  markCronSeen: (jobId?: string) => Promise<void>;
}

export const useCronStore = create<CronState>((set, get) => ({
  jobs: [],
  selectedJob: null,
  outputs: [],
  loadingOutputs: false,
  tasksTabActive: false,
  showNewJobForm: false,
  showEditJobForm: false,
  unreadCronCount: 0,
  unreadJobIds: new Set<string>(),

  setJobs: (jobs) => set((state) => ({
    jobs,
    selectedJob: state.selectedJob
      ? (jobs.find((j) => j.id === state.selectedJob!.id) ?? state.selectedJob)
      : null,
  })),

  selectJob: (job) => {
    set((state) => ({
      selectedJob: job,
      outputs: job?.id !== state.selectedJob?.id ? [] : state.outputs,
      showNewJobForm: false,
      showEditJobForm: false,
    }));
    // Mark only this specific job as seen — clears its per-job badge
    if (job) {
      get().markCronSeen(job.id);
    }
  },

  loadOutputs: async (jobId: string) => {
    set({ loadingOutputs: true });
    try {
      const res = await fetch(`/api/crons/output?job_id=${encodeURIComponent(jobId)}&limit=20`, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        set({ outputs: data.outputs || [], loadingOutputs: false });
      } else {
        set({ outputs: [], loadingOutputs: false });
      }
    } catch {
      set({ outputs: [], loadingOutputs: false });
    }
  },

  silentRefreshOutputs: async (jobId: string) => {
    try {
      const res = await fetch(`/api/crons/output?job_id=${encodeURIComponent(jobId)}&limit=20`, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        set({ outputs: data.outputs || [] });
      }
    } catch {
      // ignore
    }
  },

  setTasksTabActive: (active) => set({ tasksTabActive: active }),

  updateSelectedJob: (job) => set({ selectedJob: job }),

  refreshJobs: async () => {
    try {
      const res = await fetch('/api/crons', { credentials: 'include' });
      if (!res.ok) return;
      const data = await res.json();
      const jobs: CronJob[] = data.jobs || [];
      set((state) => {
        const updatedSelected = state.selectedJob
          ? (jobs.find((j) => j.id === state.selectedJob!.id) ?? state.selectedJob)
          : null;
        return { jobs, selectedJob: updatedSelected };
      });
      // Also reload outputs for the selected job
      const currentSelected = useCronStore.getState().selectedJob;
      if (currentSelected) {
        useCronStore.getState().silentRefreshOutputs(currentSelected.id);
      }
    } catch {
      // ignore
    }
  },

  setShowNewJobForm: (show) => set((state) => ({
    showNewJobForm: show,
    showEditJobForm: false,
    selectedJob: show ? null : state.selectedJob,
  })),

  setShowEditJobForm: (show) => set({ showEditJobForm: show, showNewJobForm: false }),

  // Compute unread count and per-job unread IDs based on completions
  // Uses per-job last_seen_cron_ts_map from settings
  refreshUnreadCount: async () => {
    try {
      const res = await cronService.recent(0);
      const completions = res.completions || [];
      const jobIds = new Set<string>(completions.map((c) => c.job_id));
      // unreadCronCount = number of jobs that have unread completions
      set({ unreadCronCount: jobIds.size, unreadJobIds: jobIds });
    } catch {
      // ignore
    }
  },

  // Mark a specific job as seen — clears its per-job badge.
  // When no unread jobs remain, the tab badge also disappears naturally.
  markCronSeen: async (jobId?: string) => {
    try {
      await cronService.seen(jobId);
      if (!jobId) {
        // Stamp all jobs — rarely used, but available as a "clear all" action
        set({ unreadCronCount: 0, unreadJobIds: new Set<string>() });
      } else {
        // Clear only this job's per-job badge
        const newIds = new Set(get().unreadJobIds);
        newIds.delete(jobId);
        set({ unreadCronCount: newIds.size, unreadJobIds: newIds });
      }
      // Update settings in the local store
      await useSettingsStore.getState().loadSettings();
    } catch {
      // ignore — badge may reappear on next refresh
    }
  },
}));