import { useEffect } from 'react';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';

/**
 * Run the app-level boot loaders that need a session cookie.
 *
 * Every `/api/*` endpoint except `/api/auth/*` is auth-protected
 * (backend/app/api/auth.py:245-250), so firing these before login answers
 * 401 — and both consequences were bugs:
 *
 *   - `loadSettings()` threw away the 401, so on a clean install the theme,
 *     language and font size fell back to the defaults until the next
 *     reload (`useSettingsStore.loadSettings` only records `error`).
 *   - `useProvidersGateStore.check()` cached the 401 as "configured", which
 *     silenced the "Add an API key to get started" gate on the first clean
 *     boot (issue #5).
 *
 * Mounted once at the App root. Re-runs when the user logs in or out, which
 * is what makes the post-login (no page reload) path work.
 */
export function useAuthenticatedBootstrap(isAuthenticated: boolean): void {
  const loadSettings = useSettingsStore((s) => s.loadSettings);
  const loadWorkspaces = useWorkspaceStore((s) => s.loadWorkspaces);

  useEffect(() => {
    if (!isAuthenticated) return;
    loadSettings();
    loadWorkspaces();
    useProvidersGateStore.getState().check();
  }, [isAuthenticated, loadSettings, loadWorkspaces]);
}
