import { useRef, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useTerminalStore } from '@/stores/useTerminalStore';
import CustomScrollbar from '@/components/common/CustomScrollbar';
import { TrashMultiIcon, Icon } from '@/components/common/Icons';

export default function TerminalPanel() {
  const { t } = useTranslation('common');
  const { terminals, activeTerminalId, addTerminal, removeTerminal, selectTerminal, renameTerminal } =
    useTerminalStore();

  // Rename state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Focus rename input when it appears
  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingId]);

  const startRename = (id: string, name: string) => {
    setRenamingId(id);
    setRenameValue(name);
  };

  const commitRename = () => {
    if (renamingId && renameValue.trim()) {
      renameTerminal(renamingId, renameValue.trim());
    }
    setRenamingId(null);
  };

  const handleRenameKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') commitRename();
    if (e.key === 'Escape') setRenamingId(null);
  };

  return (
    <div className="terminal-panel">
      {/* Header */}
      <div className="sidebar-header">
        <span className="sidebar-header-title">
          {t('tabs.terminal')}
        </span>
        <div className="sidebar-header-actions">
          <button
            className="icon-btn"
            onClick={addTerminal}
            title="New Terminal"
          >
            <Icon name="add" size={18} />
          </button>
        </div>
      </div>

      {/* Terminal list */}
      <CustomScrollbar className="flex-1">
        <ul className="terminal-list">
        {terminals.map((term) => (
          <li
            key={term.id}
            className={`sidebar-item${activeTerminalId === term.id ? ' active' : ''}`}
            onClick={() => { if (renamingId !== term.id) selectTerminal(term.id); }}
          >
            <div className="sidebar-item-text">
              {renamingId === term.id ? (
                <input
                  ref={renameInputRef}
                  className="terminal-rename-input"
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={handleRenameKey}
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <div className="sidebar-item-title-row">
                  <span className="sidebar-item-title">{term.name}</span>
                </div>
              )}
            </div>
            {renamingId !== term.id && (
              <div className="sidebar-item-actions">
                <button
                  className="sidebar-item-actions-trigger"
                  onClick={(e) => { e.stopPropagation(); removeTerminal(term.id); }}
                  title={t('sessions.delete')}
                >
                  <TrashMultiIcon size={16} />
                </button>
                <button
                  className="sidebar-item-actions-trigger"
                  onClick={(e) => { e.stopPropagation(); startRename(term.id, term.name); }}
                  title={t('sessions.rename')}
                >
                  <Icon name="edit" size={16} />
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
      </CustomScrollbar>
    </div>
  );
}
