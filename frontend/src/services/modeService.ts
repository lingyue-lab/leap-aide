import { api } from './api';

export interface ModeResponse {
  mode: 'manual' | 'smart' | 'yolo';
  smart_supported: boolean;
}

export interface SetModeResponse {
  ok: boolean;
  mode: 'manual' | 'smart' | 'yolo';
  smart_supported: boolean;
}

export async function fetchMode(sessionId: string): Promise<ModeResponse> {
  return api<ModeResponse>(`/api/session/mode?session_id=${encodeURIComponent(sessionId)}`);
}

export async function setMode(
  sessionId: string,
  mode: 'manual' | 'smart' | 'yolo'
): Promise<SetModeResponse> {
  return api<SetModeResponse>('/api/session/mode', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, mode }),
  });
}
