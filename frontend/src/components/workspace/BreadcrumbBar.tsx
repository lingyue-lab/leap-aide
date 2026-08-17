import React from 'react';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { Icon } from '@/components/common/Icons';

export default function BreadcrumbBar() {
  const { currentDir, loadDir, gitInfo } = useWorkspaceStore();

  const parts = currentDir.split('/').filter(Boolean);

  const navigateTo = (index: number) => {
    const path = '/' + parts.slice(0, index + 1).join('/');
    loadDir(path || '.');
  };

  return (
    <div className="breadcrumb-bar flex items-center gap-1 px-3 py-1 text-xs border-b border-[var(--border)] bg-[var(--input-bg)]">
      <button
        className="breadcrumb-item hover:text-[var(--blue)]"
        onClick={() => loadDir('.')}
      >
        ~
      </button>
      {parts.map((part, index) => (
        <React.Fragment key={index}>
          <span className="text-[var(--muted)]">/</span>
          <button
            className="breadcrumb-item hover:text-[var(--blue)]"
            onClick={() => navigateTo(index)}
          >
            {part}
          </button>
        </React.Fragment>
      ))}
      {gitInfo?.branch && (
        <span className="git-branch ml-auto flex items-center gap-1 text-[var(--muted)]">
          <Icon name="account_tree" size={14} />
          {gitInfo.branch}
          {gitInfo.modified !== undefined && gitInfo.modified > 0 && (
            <span className="git-modified text-[var(--gold)]">{gitInfo.modified}*</span>
          )}
        </span>
      )}
    </div>
  );
}
