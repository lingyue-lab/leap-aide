import { useTranslation } from 'react-i18next';
import { useMemoryStore } from '@/stores/useMemoryStore';
import type { MemoryFile } from '@/stores/useMemoryStore';

export default function MemoryPanel() {
  const { t } = useTranslation('common');
  const selectedFile = useMemoryStore((s) => s.selectedFile);
  const selectFile = useMemoryStore((s) => s.selectFile);

  const FILES: { key: MemoryFile; label: string; filename: string }[] = [
    { key: 'memory', label: t('memory.user'), filename: 'USER.md' },
    { key: 'project', label: t('memory.project'), filename: 'MEMORY.md' },
  ];

  return (
    <div className="memory-panel">
      {/* Header */}
      <div
        className="px-3 py-2 border-b border-[var(--border)]"
        style={{ background: 'color-mix(in srgb, var(--border) 60%, transparent)' }}
      >
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
          {t('tabs.memory')}
        </span>
      </div>

      {/* File list */}
      <div className="memory-file-list">
        {FILES.map(({ key, filename }) => {
          const isSelected = selectedFile === key;
          return (
            <div
              key={key}
              className={`flex items-center gap-2 px-3 py-2 cursor-pointer border-b border-[var(--border2)] transition-colors ${
                isSelected
                  ? 'bg-[var(--hover-bg)]'
                  : 'hover:bg-[color-mix(in_srgb,var(--hover-bg)_50%,transparent)]'
              }`}
              onClick={() => selectFile(key)}
            >
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="flex-shrink-0 text-[var(--muted)]"
              >
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              <span className="text-sm font-mono">{filename}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
