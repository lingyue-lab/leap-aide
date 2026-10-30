/**
 * Issue #5 (scope extension) — the app-level boot loaders must not run
 * before login.
 *
 * On a clean install the page is served before the user has a session
 * cookie, and every /api/* endpoint except /api/auth/* answers 401. Today
 * App.tsx fires them on mount anyway:
 *   - /api/settings + /api/workspaces fail → theme, language, font size and
 *     the workspace list fall back to defaults until the next reload
 *   - /api/providers fails → the providers-gate cache is poisoned (the part
 *     of issue #5 fixed in useProvidersGateStore)
 *
 * All three must be deferred until `isAuthenticated` is true, and must run
 * when the user logs in (login does not reload the page).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useAuthenticatedBootstrap } from '@/hooks/useAuthenticatedBootstrap';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';

const loadSettings = vi.fn().mockResolvedValue(undefined);
const loadWorkspaces = vi.fn().mockResolvedValue(undefined);
const check = vi.fn().mockResolvedValue(false);

describe('useAuthenticatedBootstrap', () => {
  beforeEach(() => {
    loadSettings.mockClear();
    loadWorkspaces.mockClear();
    check.mockClear();
    useSettingsStore.setState({ loadSettings } as never);
    useWorkspaceStore.setState({ loadWorkspaces } as never);
    useProvidersGateStore.setState({ check } as never);
  });

  it('does not touch auth-protected endpoints before login', () => {
    renderHook(() => useAuthenticatedBootstrap(false));
    expect(loadSettings).not.toHaveBeenCalled();
    expect(loadWorkspaces).not.toHaveBeenCalled();
    expect(check).not.toHaveBeenCalled();
  });

  it('loads settings, workspaces and the providers-gate state after login', () => {
    renderHook(() => useAuthenticatedBootstrap(true));
    expect(loadSettings).toHaveBeenCalledTimes(1);
    expect(loadWorkspaces).toHaveBeenCalledTimes(1);
    expect(check).toHaveBeenCalledTimes(1);
  });

  it('runs when the user logs in without a page reload', () => {
    const { rerender } = renderHook(
      ({ authed }: { authed: boolean }) => useAuthenticatedBootstrap(authed),
      { initialProps: { authed: false } },
    );
    expect(loadSettings).not.toHaveBeenCalled();

    rerender({ authed: true });
    expect(loadSettings).toHaveBeenCalledTimes(1);
    expect(loadWorkspaces).toHaveBeenCalledTimes(1);
    expect(check).toHaveBeenCalledTimes(1);
  });
});
