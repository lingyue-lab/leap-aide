import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import type { Workspace } from '@/types';

export interface WorkspaceDisplay {
  /** Display label — workspace name (with "(EXT)" suffix if external), or fallback */
  label: string;
  /** Matched workspace object, or null if unassigned / not found */
  workspace: Workspace | null;
  /** True when workspacePath is empty/falsy */
  isUnassigned: boolean;
}

/**
 * Pure utility: resolve a workspace path to display info.
 *
 * Call this inside existing useMemo / useCallback closures to avoid
 * hook-ordering issues (e.g. inside SessionList grouping logic).
 */
export function resolveWorkspaceDisplay(
  workspacePath: string | undefined,
  workspaces: Workspace[],
  noWorkspaceLabel: string,
): WorkspaceDisplay {
  if (!workspacePath) {
    return { label: noWorkspaceLabel, workspace: null, isUnassigned: true };
  }

  const match = workspaces.find((w) => w.path === workspacePath) ?? null;
  const baseName = match?.display_name || match?.name || null;

  if (baseName) {
    const label = match!.external ? `${baseName}(EXT)` : baseName;
    return { label, workspace: match, isUnassigned: false };
  }

  // Fallback: last path segment
  const fallback = workspacePath.split('/').filter(Boolean).pop() || workspacePath;
  return { label: fallback, workspace: null, isUnassigned: false };
}

/**
 * Hook wrapper: resolve a single workspace path to consistent display info.
 *
 * Used by components that only need to resolve one path at a time
 * (e.g. the composer footer workspace chip).
 */
export function useWorkspaceDisplay(workspacePath?: string): WorkspaceDisplay {
  const { t } = useTranslation('common');
  const workspaces = useWorkspaceStore((s) => s.workspaces);

  return useMemo(() => {
    const noWsLabel = t('sessions.no_workspace') || 'No Workspace';
    return resolveWorkspaceDisplay(workspacePath, workspaces, noWsLabel);
  }, [workspacePath, workspaces, t]);
}
