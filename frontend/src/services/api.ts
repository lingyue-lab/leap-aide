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
      body: JSON.stringify({ id: jobId, ...updates }),
    });
  },

  delete: async (jobId: string) => {
    return api('/api/crons/delete', {
      method: 'POST',
      body: JSON.stringify({ id: jobId }),
    });
  },

  run: async (jobId: string) => {
    return api('/api/crons/run', {
      method: 'POST',
      body: JSON.stringify({ id: jobId }),
    });
  },

  pause: async (jobId: string) => {
    return api('/api/crons/pause', {
      method: 'POST',
      body: JSON.stringify({ id: jobId }),
    });
  },

  resume: async (jobId: string) => {
    return api('/api/crons/resume', {
      method: 'POST',
      body: JSON.stringify({ id: jobId }),
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
};

// Memory API 服务
export const memoryService = {
  get: async () => {
    return api<import('@/types').MemoryData>('/api/memory');
  },

  write: async (section: 'memory' | 'project', content: string) => {
    return api('/api/memory/write', {
      method: 'POST',
      body: JSON.stringify({ section, content }),
    });
  },
};

// Profiles API 服务
export const profilesService = {
  list: async () => {
    return api<{ profiles: import('@/types').Profile[]; active: string }>('/api/profiles');
  },

  getActive: async () => {
    return api<{ name: string; path: string }>('/api/profile/active');
  },

  switch: async (name: string) => {
    return api('/api/profile/switch', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
  },

  create: async (
    name: string,
    clone_from?: string,
    clone_config?: boolean,
    base_url?: string,
    api_key?: string
  ) => {
    return api('/api/profile/create', {
      method: 'POST',
      body: JSON.stringify({ name, clone_from, clone_config, base_url, api_key }),
    });
  },

  delete: async (name: string) => {
    return api('/api/profile/delete', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
  },
};
