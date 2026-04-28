import { useEffect } from 'react';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

export default function FileTree() {
  const { entries, loading, loadDir, setPreview } = useWorkspaceStore();

  useEffect(() => {
    loadDir('.');
  }, [loadDir]);

  const handleEntryClick = (entry: { name: string; path: string; is_dir: boolean }) => {
    if (entry.is_dir) {
      loadDir(entry.path);
    } else {
      setPreview(entry.path);
    }
  };

  if (entries.length === 0 && !loading) {
    return (
      <div className="file-tree text-center py-4 text-[var(--muted)] text-sm">
        Empty directory
      </div>
    );
  }

  return (
    <div className="file-tree">
      {loading && (
        <div className="flex items-center justify-center py-4 text-[var(--muted)]">
          <svg className="animate-spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" strokeOpacity="0.25" />
            <path d="M12 2a10 10 0 0 1 10 10" />
          </svg>
        </div>
      )}
      {entries.map((entry) => (
        <div
          key={entry.path}
          className="file-item"
          onClick={() => handleEntryClick(entry)}
        >
          {entry.is_dir ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
          )}
          <span className="truncate">{entry.name}</span>
          {entry.size !== undefined && !entry.is_dir && (
            <span className="text-[var(--muted)] text-xs ml-auto">{formatSize(entry.size)}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}M`;
}
