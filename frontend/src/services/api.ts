// API 基础封装
const API_BASE = '';

export interface ApiError {
  message: string;
  status?: number;
}

export async function api<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    credentials: 'include',
  });

  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const error = await response.json();
      message = error.message || message;
    } catch {
      // Ignore JSON parse errors
    }
    const error: ApiError = { message, status: response.status };
    throw error;
  }

  return response.json();
}

// Chat API 服务
export const chatService = {
  startChat: async (sessionId: string, message: string, model: string, workspace?: string, attachments?: string[]) => {
    return api<{ stream_id: string }>('/api/chat/start', {
      method: 'POST',
      body: JSON.stringify({
        session_id: sessionId,
        message,
        model,
        workspace,
        attachments,
      }),
    });
  },

  createSSEConnection: (streamId: string): EventSource => {
    const url = `/api/chat/stream?stream_id=${encodeURIComponent(streamId)}`;
    return new EventSource(url, { withCredentials: true });
  },

  createReplaySSEConnection: (streamId: string): EventSource => {
    // Connects to the replay endpoint which first sends all past events
    // (replay_start → <history> → replay_end) then continues with live events.
    // Used when reconnecting after a page refresh so the user sees all tokens
    // that were already produced before the refresh.
    const url = `/api/chat/stream/replay?stream_id=${encodeURIComponent(streamId)}`;
    return new EventSource(url, { withCredentials: true });
  },

  checkStreamStatus: async (streamId: string) => {
    return api<{ active: boolean; session_id?: string }>(
      `/api/chat/stream/status?stream_id=${encodeURIComponent(streamId)}`
    );
  },

  cancelStream: async (streamId: string) => {
    // Use GET with query param (routes.py line 1048-1053)
    return api(`/api/chat/cancel?stream_id=${encodeURIComponent(streamId)}`);
  },
};

// Session API 服务
export const sessionService = {
  getSessions: async () => {
    return api<{ sessions: Array<{ session_id: string; title: string; created_at: number; pinned?: boolean }> }>(
      '/api/sessions'
    );
  },

  getSession: async (sessionId: string) => {
    return api<{ session: import('@/types').Session }>(
      `/api/session?session_id=${encodeURIComponent(sessionId)}`
    );
  },

  createSession: async () => {
    return api<{ session: import('@/types').Session }>('/api/session', {
      method: 'POST',
    });
  },

  renameSession: async (sessionId: string, title: string) => {
    return api('/api/session/rename', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, title }),
    });
  },

  deleteSession: async (sessionId: string) => {
    return api('/api/session/delete', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    });
  },

  pinSession: async (sessionId: string, pinned: boolean) => {
    return api('/api/session/pin', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, pinned }),
    });
  },

  clearSession: async (sessionId: string) => {
    return api('/api/session/clear', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId }),
    });
  },

  updateModel: async (sessionId: string, model: string) => {
    return api<{ session: import('@/types').Session }>('/api/session/update', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, model }),
    });
  },

  markSessionSeen: async (sessionId: string, msgCount: number) => {
    return api<{ ok: boolean }>('/api/session/seen', {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, msg_count: msgCount }),
    });
  },

  compressSession: async (sessionId: string, focusTopic?: string) => {
    return api<{ ok: boolean; session: import('@/types').Session; summary?: object }>(
      '/api/session/compress',
      {
        method: 'POST',
        body: JSON.stringify({
          session_id: sessionId,
          ...(focusTopic ? { focus_topic: focusTopic } : {}),
        }),
      }
    );
  },

  clearHistory: async (sessionId: string, keepLast = 0) => {
    return api<{ ok: boolean; session_id: string; kept: number }>(
      '/api/session/clear',
      {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId, keep_last: keepLast }),
      }
    );
  },
};

// Workspace API 服务
export const workspaceService = {
  listDir: async (path: string) => {
    return api<import('@/types').DirEntry>(
      `/api/workspace/list?path=${encodeURIComponent(path)}`
    );
  },

  readFile: async (path: string) => {
    const response = await fetch(
      `/api/workspace/file?path=${encodeURIComponent(path)}`,
      { credentials: 'include' }
    );
    if (!response.ok) throw new Error('Failed to read file');
    return response.text();
  },

  saveFile: async (path: string, content: string) => {
    return api('/api/workspace/file', {
      method: 'POST',
      body: JSON.stringify({ path, content }),
    });
  },

  createFile: async (path: string, isDir: boolean = false) => {
    return api('/api/workspace/new', {
      method: 'POST',
      body: JSON.stringify({ path, is_dir: isDir }),
    });
  },

  moveFile: async (src: string, dst: string) => {
    return api('/api/workspace/move', {
      method: 'POST',
      body: JSON.stringify({ src, dst }),
    });
  },

  deleteFile: async (path: string) => {
    return api('/api/workspace/delete', {
      method: 'POST',
      body: JSON.stringify({ path }),
    });
  },
};

// Settings API 服务
export const settingsService = {
  getSettings: async () => {
    return api<import('@/types').Settings>('/api/settings');
  },

  saveSettings: async (settings: Partial<import('@/types').Settings>) => {
    return api('/api/settings', {
      method: 'POST',
      body: JSON.stringify(settings),
    });
  },
};

// Models API 服务
export const modelsService = {
  getModels: async () => {
    return api<import('@/types').ModelsResponse>('/api/models');
  },

  getLiveModels: async (provider: string) => {
    return api<{ models: Array<{ id: string; label?: string }> }>(
      `/api/models/live?provider=${encodeURIComponent(provider)}`
    );
  },
};

// Config file API 服务
export const configFileService = {
  getRaw: async (): Promise<string> => {
    const res = await fetch('/api/config/raw', { credentials: 'include' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.text();
  },
  saveRaw: async (content: string): Promise<void> => {
    const res = await fetch('/api/config/raw', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || 'Save failed');
  },
};

// Providers API 服务
export const providersService = {
  getProviders: async (): Promise<{ providers: import('@/types').Provider[]; active_provider: string | null }> => {
    return api('/api/providers');
  },

  setKey: async (provider: string, apiKey: string): Promise<{ ok: boolean; error?: string }> => {
    return api('/api/providers', {
      method: 'POST',
      body: JSON.stringify({ provider, api_key: apiKey }),
    });
  },

  deleteKey: async (provider: string): Promise<{ ok: boolean; error?: string }> => {
    return api('/api/providers/delete', {
      method: 'POST',
      body: JSON.stringify({ provider }),
    });
  },
};

// Approval API 服务
export const approvalService = {
  getPending: async (sessionId: string) => {
    return api<{ pending?: import('@/types').ApprovalRequest; pending_count?: number }>(
      `/api/approval/pending?session_id=${encodeURIComponent(sessionId)}`
    );
  },

  respond: async (sessionId: string, choice: 'once' | 'session' | 'always' | 'deny', approvalId?: string | null) => {
    return api('/api/approval/respond', {
      method: 'POST',
      body: JSON.stringify({
        session_id: sessionId,
        choice,
        approval_id: approvalId,
      }),
    });
  },
};

// Clarify API 服务
export const clarifyService = {
  getPending: async (sessionId: string) => {
    return api<{ pending?: import('@/types').ClarifyRequest }>(
      `/api/clarify/pending?session_id=${encodeURIComponent(sessionId)}`
    );
  },

  respond: async (sessionId: string, response: string) => {
    return api('/api/clarify/respond', {
      method: 'POST',
      body: JSON.stringify({
        session_id: sessionId,
        response,
      }),
    });
  },
};

// Cron/Tasks API 服务
export const cronService = {
  list: async () => {
    return api<{ jobs: import('@/types').CronJob[] }>('/api/crons');
  },

  create: async (job: import('@/types').CronCreateRequest) => {
    return api<{ ok: boolean; job: import('@/types').CronJob }>('/api/crons/create', {
      method: 'POST',
      body: JSON.stringify(job),
    });
  },

  update: async (jobId: string, updates: Partial<import('@/types').CronJob>) => {
    return api('/api/crons/update', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, ...updates }),
    });
  },

  delete: async (jobId: string) => {
    return api('/api/crons/delete', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId }),
    });
  },

  run: async (jobId: string) => {
    return api('/api/crons/run', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId }),
    });
  },

  pause: async (jobId: string) => {
    return api('/api/crons/pause', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId }),
    });
  },

  resume: async (jobId: string) => {
    return api('/api/crons/resume', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId }),
    });
  },

  getOutput: async (jobId: string) => {
    return api<{ output: string }>('/api/crons/output', {
      method: 'POST',
      body: JSON.stringify({ id: jobId }),
    });
  },
};

// Skills API 服务
export const skillsService = {
  list: async () => {
    return api<{ skills: import('@/types').Skill[] }>('/api/skills');
  },

  getContent: async (name: string, file?: string) => {
    const params = new URLSearchParams({ name });
    if (file) params.append('file', file);
    return api<{ content: string; linked_files?: Record<string, string> }>(
      `/api/skills/content?${params.toString()}`
    );
  },

  save: async (name: string, content: string, category?: string) => {
    return api('/api/skills/save', {
      method: 'POST',
      body: JSON.stringify({ name, content, category }),
    });
  },

  delete: async (name: string) => {
    return api('/api/skills/delete', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
  },

  getFiles: async (name: string) => {
    return api<{ tree: import('@/types').SkillFileNode[]; skill_dir: string }>(
      `/api/skills/files?${new URLSearchParams({ name })}`
    );
  },

  saveFile: async (name: string, filePath: string, content: string) => {
    return api<{ ok: boolean }>(
      `/api/skills/content`,
      {
        method: 'POST',
        body: JSON.stringify({ name, file: filePath, content }),
      }
    );
  },

  importDir: async (
    skillName: string,
    category: string | undefined,
    files: { path: string; file: File }[],
    overwrite = false
  ): Promise<{ ok?: boolean; name?: string; file_count?: number; conflict?: boolean }> => {
    const formData = new FormData();
    formData.append('skill_name', skillName);
    if (category) formData.append('category', category);
    formData.append('overwrite', overwrite ? 'true' : 'false');
    files.forEach(({ path, file }, i) => {
      formData.append(`file_${i}`, file);
      formData.append(`path_${i}`, path);
    });
    const response = await fetch('/api/skills/import', {
      method: 'POST',
      body: formData,
      credentials: 'include',
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 409 && data.conflict) {
      return { conflict: true, name: data.name };
    }
    if (!response.ok) {
      throw { message: data.message || `HTTP ${response.status}`, status: response.status };
    }
    return data;
  },
};

// Memory API 服务
export const memoryService = {
  get: async (): Promise<import('@/types').MemoryData> => {
    const raw = await api<{
      memory: string;
      user: string;
      memory_mtime: number | null;
      user_mtime: number | null;
    }>('/api/memory');
    return {
      user_memory: {
        section: 'memory',
        content: raw.user ?? '',
        updated_at: raw.user_mtime ?? undefined,
      },
      project_memory: {
        section: 'project',
        content: raw.memory ?? '',
        updated_at: raw.memory_mtime ?? undefined,
      },
    };
  },

  write: async (section: 'memory' | 'project', content: string) => {
    // backend uses 'memory' for MEMORY.md and 'user' for USER.md
    const backendSection = section === 'project' ? 'memory' : 'user';
    return api('/api/memory/write', {
      method: 'POST',
      body: JSON.stringify({ section: backendSection, content }),
    });
  },
};


