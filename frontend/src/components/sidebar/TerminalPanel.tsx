import { useRef, useState, useEffect } from 'react';
import { useTerminalStore } from '@/stores/useTerminalStore';
import type { TerminalInstance } from '@/stores/useTerminalStore';

export default function TerminalPanel() {
  const { terminals, activeTerminalId, addTerminal, removeTerminal, selectTerminal, renameTerminal } =
    useTerminalStore();

  // Three-dot menu state
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
  const menuRef = useRef<HTMLDivElement>(null);

  // Rename state
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Close menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpenId(null);
      }
    };
    if (menuOpenId) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menuOpenId]);

  // Focus rename input when it appears
  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingId]);

  const openMenu = (e: React.MouseEvent, t: TerminalInstance) => {
    e.stopPropagation();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenuPosition({ top: rect.bottom + window.scrollY, left: rect.left + window.scrollX });
    setMenuOpenId(menuOpenId === t.id ? null : t.id);
  };

  const startRename = (t: TerminalInstance) => {
    setMenuOpenId(null);
    setRenamingId(t.id);
    setRenameValue(t.name);
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

  const handleDelete = (t: TerminalInstance) => {
    setMenuOpenId(null);
    removeTerminal(t.id);
  };

  return (
    <div className="terminal-panel">
      {/* Header */}
      <div className="flex items-center justify-between p-2 border-b border-[var(--border)]">
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
          Terminals
        </span>
        <button
          className="session-actions-trigger"
          style={{ opacity: 1, width: 24, height: 24 }}
          onClick={addTerminal}
          title="New Terminal"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>
      </div>

      {/* Terminal list */}
      <ul className="terminal-list">
        {terminals.map((t) => (
          <li
            key={t.id}
            className={`terminal-list-item ${activeTerminalId === t.id ? 'active' : ''}`}
            onClick={() => { if (renamingId !== t.id) selectTerminal(t.id); }}
          >
            {/* Terminal icon */}
            <svg className="terminal-list-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="4 17 10 11 4 5" />
              <line x1="12" y1="19" x2="20" y2="19" />
            </svg>

            {/* Name / rename input */}
            {renamingId === t.id ? (
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
              <span className="terminal-list-name">{t.name}</span>
            )}

            {/* Three-dot menu trigger */}
            {renamingId !== t.id && (
              <button
                className={`session-actions-trigger ${menuOpenId === t.id ? 'active' : ''}`}
                onClick={(e) => openMenu(e, t)}
                aria-label="More actions"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="1" />
                  <circle cx="12" cy="5" r="1" />
                  <circle cx="12" cy="19" r="1" />
                </svg>
              </button>
            )}
          </li>
        ))}
      </ul>

      {/* Dropdown menu */}
      {menuOpenId && (
        <div
          ref={menuRef}
          className="session-action-menu open"
          style={{
            position: 'fixed',
            top: menuPosition.top,
            left: menuPosition.left,
            minWidth: 140,
            zIndex: 9999,
          }}
        >
          <button
            className="session-action-opt"
            onClick={() => {
              const term = terminals.find((t) => t.id === menuOpenId);
              if (term) startRename(term);
            }}
          >
            <span className="ws-opt-action">
              <svg className="ws-opt-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
              </svg>
              <span className="ws-opt-name">Rename</span>
            </span>
          </button>
          <button
            className="session-action-opt danger"
            onClick={() => {
              const term = terminals.find((t) => t.id === menuOpenId);
              if (term) handleDelete(term);
            }}
          >
            <span className="ws-opt-action">
              <svg className="ws-opt-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
              </svg>
              <span className="ws-opt-name">Delete</span>
            </span>
          </button>
        </div>
      )}
    </div>
  );
}
