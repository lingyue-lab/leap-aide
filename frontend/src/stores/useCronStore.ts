import { create } from 'zustand';
import type { CronJob } from '@/types';

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

  setJobs: (jobs: CronJob[]) => void;
  selectJob: (job: CronJob | null) => void;
  loadOutputs: (jobId: string) => Promise<void>;
  silentRefreshOutputs: (jobId: string) => Promise<void>;
  refreshJobs: () => Promise<void>;
  setTasksTabActive: (active: boolean) => void;
  updateSelectedJob: (job: CronJob) => void;
  setShowNewJobForm: (show: boolean) => void;
  setShowEditJobForm: (show: boolean) => void;
}

export const useCronStore = create<CronState>((set) => ({
  jobs: [],
  selectedJob: null,
  outputs: [],
  loadingOutputs: false,
  tasksTabActive: false,
  showNewJobForm: false,
  showEditJobForm: false,

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
}));
