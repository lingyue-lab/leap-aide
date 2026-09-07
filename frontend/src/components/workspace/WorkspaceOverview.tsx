import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { ChevronRightIcon, FolderOpenIcon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import type { GitCommit } from '@/types';

// ── Helpers ──────────────────────────────────────────────────────

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return '—';
  }
}

/* M3 shape token for cards / dialogs */
const SHAPE_MD = 'var(--md-shape-md)';

// ── Component ────────────────────────────────────────────────────

export default function WorkspaceOverview() {
  const { t } = useTranslation('common');
  const {
    currentWorkspace,
    workspaceInfo,
    workspaceInfoLoading,
    showFileTree,
    setShowFileTree,
  } = useWorkspaceStore();

  const [expandedCommits, setExpandedCommits] = useState<Set<string>>(new Set());

  // Reset expanded state when workspace changes
  useEffect(() => {
    setExpandedCommits(new Set());
  }, [currentWorkspace]);

  if (!currentWorkspace) return null;

  const wsName = currentWorkspace.split('/').pop() || currentWorkspace;

  const toggleCommit = (hash: string) => {
    setExpandedCommits((prev) => {
      const next = new Set(prev);
      if (next.has(hash)) next.delete(hash);
      else next.add(hash);
      return next;
    });
  };

  return (
    <div className="task-detail flex-1 flex flex-col overflow-hidden h-full">
      <div className="topbar">
        <div className="flex-1 min-w-0 overflow-hidden">
          <div className="topbar-title">{wsName}</div>
          <div className="topbar-meta">{currentWorkspace}</div>
        </div>
        <div className="topbar-chips">
          <button
            className={`icon-btn${showFileTree ? ' active' : ''}`}
            onClick={() => setShowFileTree(!showFileTree)}
            title={showFileTree ? 'Hide file tree' : 'Show file tree'}
          >
            <FolderOpenIcon />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div style={{ maxWidth: 672, marginLeft: 'auto', marginRight: 'auto' }}>

          {workspaceInfoLoading && (
            <LoadingSpinner mode="inline" />
          )}

          {!workspaceInfoLoading && !workspaceInfo && (
            <div className="text-sm text-[var(--muted)] text-center py-8 italic">
              {t('workspaces.failed_to_load')}
            </div>
          )}

          {workspaceInfo && (
            <>
              {/* Info Grid */}
              <div className="grid gap-3 mb-5">

                {/* Created */}
                <div
                  className="p-4 border border-[var(--border2)]"
                  style={{ borderRadius: SHAPE_MD, background: 'var(--md-surface-container-low)' }}
                >
                  <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('workspaces.created')}</div>
                  <div className="text-base text-[var(--text)]">{formatDate(workspaceInfo.created)}</div>
                </div>

                {/* Stats */}
                <div
                  className="p-4 border border-[var(--border2)]"
                  style={{ borderRadius: SHAPE_MD, background: 'var(--md-surface-container-low)' }}
                >
                  <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('workspaces.summary')}</div>
                  <div className="text-base text-[var(--text)]">
                    {t('workspaces.files_and_dirs', { fileCount: workspaceInfo.file_count, dirCount: workspaceInfo.dir_count })}
                  </div>
                  <div className="text-sm text-[var(--muted)] mt-0.5">
                    {t('workspaces.total_size', { size: formatBytes(workspaceInfo.total_size) })}
                  </div>
                </div>

                {/* Git Info */}
                {workspaceInfo.git && (
                  <div
                    className="p-4 border border-[var(--border2)]"
                    style={{ borderRadius: SHAPE_MD, background: 'var(--md-surface-container-low)' }}
                  >
                    <div className="text-base font-medium text-[var(--muted)] mb-0.5">{t('workspaces.git')}</div>
                    <div className="text-base text-[var(--text)]">
                      {t('workspaces.branch', { branch: workspaceInfo.git.branch })}
                    </div>
                    <div className="text-sm text-[var(--muted)] mt-0.5">
                      {workspaceInfo.git.dirty > 0 && `${t('workspaces.changed', { count: workspaceInfo.git.dirty })} · `}
                      {workspaceInfo.git.modified > 0 && `${t('workspaces.modified', { count: workspaceInfo.git.modified })} · `}
                      {workspaceInfo.git.untracked > 0 && `${t('workspaces.untracked', { count: workspaceInfo.git.untracked })} · `}
                      {workspaceInfo.git.ahead > 0 && `${t('workspaces.ahead', { count: workspaceInfo.git.ahead })} · `}
                      {workspaceInfo.git.behind > 0 && `${t('workspaces.behind', { count: workspaceInfo.git.behind })} · `}
                      {workspaceInfo.git.dirty === 0 &&
                       workspaceInfo.git.modified === 0 &&
                       workspaceInfo.git.untracked === 0 &&
                       workspaceInfo.git.ahead === 0 &&
                       workspaceInfo.git.behind === 0 && t('workspaces.working_tree_clean')}
                    </div>
                  </div>
                )}
              </div>

              {/* Git Log */}
              {workspaceInfo.git && workspaceInfo.git.log.length > 0 && (
                <div>
                  <div className="text-base font-medium text-[var(--muted)] mb-2">
                    {t('workspaces.recent_commits')}
                  </div>

                  {workspaceInfo.git.log.map((commit: GitCommit) => {
                    const isExpanded = expandedCommits.has(commit.hash);

                    return (
                      <div
                        key={commit.hash}
                        className="mb-2 border border-[var(--border2)] overflow-hidden"
                        style={{ borderRadius: SHAPE_MD }}
                      >
                        <button
                          className="w-full flex items-center justify-between px-3 text-left transition-colors"
                          style={{
                            minHeight: 48,
                            borderRadius: SHAPE_MD,
                            background: 'var(--md-surface-container-low)',
                          }}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.background = 'var(--md-state-hover)';
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.background = 'var(--md-surface-container-low)';
                          }}
                          onClick={() => toggleCommit(commit.hash)}
                        >
                          <div className="flex-1 min-w-0 flex items-center gap-2">
                            <span className="text-sm text-[var(--muted)] font-mono shrink-0">
                              {commit.hash}
                            </span>
                            <span className="text-base text-[var(--text)] truncate">
                              {commit.message}
                            </span>
                          </div>
                          <ChevronRightIcon
                            size={18}
                            className={`text-[var(--muted)] transition-transform duration-200 shrink-0 ml-2 ${isExpanded ? 'rotate-90' : ''}`}
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
                            <div
                              className="border-t border-[var(--border2)] px-4 py-3"
                              style={{ background: 'var(--md-surface-container)' }}
                            >
                              <div className="text-base text-[var(--text)] mb-2 whitespace-pre-wrap break-words">
                                {commit.message}
                              </div>
                              <div className="text-sm text-[var(--muted)]">
                                <span className="font-medium text-[var(--text)]">{commit.author}</span>
                                <span className="mx-2">·</span>
                                <span>{commit.date}</span>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
