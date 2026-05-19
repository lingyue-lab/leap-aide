import { useCallback, useEffect, useRef, useState } from 'react';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

interface WorkspaceFormProps {
  onSuccess: () => void;
  onCancel: () => void;
}

export default function WorkspaceForm({ onSuccess, onCancel }: WorkspaceFormProps) {
  const { addWorkspace } = useWorkspaceStore();

  const [addPath, setAddPath] = useState('');
  const [addName, setAddName] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // path suggestions
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [activeSugIdx, setActiveSugIdx] = useState(-1);
  const suggestTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pathInputRef = useRef<HTMLInputElement>(null);
  const suggestionsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    pathInputRef.current?.focus();
  }, []);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        suggestionsRef.current && !suggestionsRef.current.contains(e.target as Node) &&
        pathInputRef.current && !pathInputRef.current.contains(e.target as Node)
      ) {
        setSuggestionsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const fetchSuggestions = useCallback(async (prefix: string) => {
    try {
      const res = await fetch(
        `/api/workspaces/suggest?prefix=${encodeURIComponent(prefix)}`,
        { credentials: 'include' }
      );
      if (!res.ok) return;
      const data = await res.json();
      setSuggestions(data.suggestions || []);
      setSuggestionsOpen((data.suggestions || []).length > 0);
      setActiveSugIdx(-1);
    } catch {
      /* silent */
    }
  }, []);

  const handlePathChange = (value: string) => {
    setAddPath(value);
    if (suggestTimer.current) clearTimeout(suggestTimer.current);
    if (!value.trim()) {
      setSuggestions([]);
      setSuggestionsOpen(false);
      return;
    }
    suggestTimer.current = setTimeout(() => fetchSuggestions(value), 200);
  };

  const handleSelectSuggestion = (path: string) => {
    const withSlash = path.endsWith('/') ? path : path + '/';
    setAddPath(withSlash);
    setSuggestionsOpen(false);
    setActiveSugIdx(-1);
    fetchSuggestions(withSlash);
    pathInputRef.current?.focus();
  };

  const handlePathKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!suggestionsOpen || suggestions.length === 0) {
      if (e.key === 'Enter') { e.preventDefault(); handleSubmit(); }
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveSugIdx((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveSugIdx((i) => Math.max(i - 1, -1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (activeSugIdx >= 0 && suggestions[activeSugIdx]) {
        handleSelectSuggestion(suggestions[activeSugIdx]);
      } else {
        setSuggestionsOpen(false);
        handleSubmit();
      }
    } else if (e.key === 'Escape') {
      setSuggestionsOpen(false);
    } else if (e.key === 'Tab') {
      e.preventDefault();
      if (activeSugIdx >= 0 && suggestions[activeSugIdx]) {
        handleSelectSuggestion(suggestions[activeSugIdx]);
      } else if (suggestions.length === 1) {
        handleSelectSuggestion(suggestions[0]);
      }
    }
  };

  const handleSubmit = async () => {
    const path = addPath.trim();
    if (!path) {
      setError('Path is required');
      return;
    }
    setSuggestionsOpen(false);
    setSubmitting(true);
    setError('');
    try {
      await addWorkspace(path, addName.trim() || undefined);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add workspace');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex flex-col overflow-hidden" style={{ flex: 1 }}>
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)] bg-[var(--header-bg)]">
        <div className="text-sm font-semibold">New Workspace</div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {error && (
            <span className="text-xs text-[var(--accent)] self-center max-w-[280px] truncate">{error}</span>
          )}
          <button
            className="cron-btn run text-xs px-3 py-1"
            onClick={handleSubmit}
            disabled={!addPath.trim() || submitting}
          >
            {submitting ? 'Adding…' : 'Add'}
          </button>
          <button className="cron-btn text-xs px-3 py-1" onClick={onCancel} disabled={submitting}>
            Cancel
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-5 max-w-2xl mx-auto w-full">

        {/* Path field */}
        <div>
          <label className="block text-xs font-medium text-[var(--muted)] mb-1">
            Workspace Path
            <span className="text-[10px] font-normal ml-1">(absolute path to a directory)</span>
          </label>
          <div className="relative">
            <input
              ref={pathInputRef}
              type="text"
              placeholder="/home/user/my-project"
              value={addPath}
              onChange={(e) => handlePathChange(e.target.value)}
              onKeyDown={handlePathKeyDown}
              onFocus={() => addPath.trim() && suggestions.length > 0 && setSuggestionsOpen(true)}
              className="w-full px-3 py-2 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded-lg outline-none focus:border-[var(--accent)] transition-colors"
            />
            {suggestionsOpen && suggestions.length > 0 && (
              <div
                ref={suggestionsRef}
                className="absolute left-0 right-0 top-full mt-1 z-50 rounded-lg border border-[var(--border)] bg-[var(--sidebar-bg,#1e1e1e)] shadow-xl overflow-hidden"
                style={{ maxHeight: 220, overflowY: 'auto' }}
              >
                {suggestions.map((s, i) => (
                  <button
                    key={s}
                    className={`w-full text-left px-3 py-1.5 text-xs truncate transition-colors flex items-center gap-2 ${
                      i === activeSugIdx
                        ? 'bg-[var(--hover-bg)] text-[var(--text)]'
                        : 'text-[var(--muted)] hover:bg-[var(--hover-bg)] hover:text-[var(--text)]'
                    }`}
                    onMouseDown={(e) => { e.preventDefault(); handleSelectSuggestion(s); }}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="flex-shrink-0 opacity-60">
                      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                    </svg>
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
          <p className="text-[11px] text-[var(--muted)] mt-1.5">
            Start typing to see directory suggestions. Use ↑↓ to navigate, Tab or Enter to select.
          </p>
        </div>

        {/* Display name field */}
        <div>
          <label className="block text-xs font-medium text-[var(--muted)] mb-1">
            Display Name
            <span className="text-[10px] font-normal ml-1">(optional — defaults to folder name)</span>
          </label>
          <input
            type="text"
            placeholder={addPath ? addPath.split('/').filter(Boolean).pop() || '' : 'e.g. My Project'}
            value={addName}
            onChange={(e) => setAddName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
            className="w-full px-3 py-2 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded-lg outline-none focus:border-[var(--accent)] transition-colors"
          />
        </div>

        {/* Preview */}
        {addPath.trim() && (
          <div className="rounded-lg border border-[var(--border2)] bg-[var(--hover-bg)] p-4">
            <div className="text-xs font-medium text-[var(--muted)] mb-3 uppercase tracking-wider">Preview</div>
            <div className="flex items-center gap-3">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-[var(--accent)] flex-shrink-0">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
              </svg>
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">
                  {addName.trim() || addPath.trim().split('/').filter(Boolean).pop() || addPath.trim()}
                </div>
                <div className="text-xs text-[var(--muted)] truncate mt-0.5">{addPath.trim()}</div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
