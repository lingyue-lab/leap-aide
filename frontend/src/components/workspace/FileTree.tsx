import { useEffect } from 'react';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import formatSize from '@/utils/formatSize';
import { Icon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';

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
        <LoadingSpinner mode="inline" minimal />
      )}
      {entries.map((entry) => (
        <div
          key={entry.path}
          className="file-item"
          onClick={() => handleEntryClick(entry)}
        >
          {entry.is_dir ? (
            <Icon name="folder_open" size={18} />
          ) : (
            <Icon name="description" size={18} />
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

