import { useTranslation } from 'react-i18next';
import { useMemoryStore } from '@/stores/useMemoryStore';
import type { MemoryFile } from '@/stores/useMemoryStore';
import CustomScrollbar from '@/components/common/CustomScrollbar';

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
      <div className="sidebar-header">
        <span className="sidebar-header-title">
          {t('tabs.memory')}
        </span>
      </div>

      {/* File list */}
      <CustomScrollbar className="flex-1">
        {FILES.map(({ key, filename }) => {
          const isSelected = selectedFile === key;
          return (
            <div
              key={key}
              className={`sidebar-item${isSelected ? ' active' : ''}`}
              onClick={() => selectFile(key)}
            >
              <div className="sidebar-item-text">
                <div className="sidebar-item-title-row">
                  <span className="sidebar-item-title">{filename}</span>
                </div>
              </div>
            </div>
          );
        })}
      </CustomScrollbar>
    </div>
  );
}
