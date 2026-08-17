import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import FormPage, { FormActions } from '@/components/common/FormPage';
import { FormField, TextField } from '@/components/common/FormField';
import { Icon } from '@/components/common/Icons';

interface WorkspaceFormProps {
  onSuccess: () => void;
  onCancel: () => void;
}

export default function WorkspaceForm({ onSuccess, onCancel }: WorkspaceFormProps) {
  const { t } = useTranslation('common');
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
    <FormPage
      title={t('workspaces.new_workspace')}
      error={error}
      actions={
        <FormActions
          onSubmit={handleSubmit}
          onCancel={onCancel}
          busy={submitting}
          submitDisabled={!addPath.trim()}
        />
      }
    >

        {/* Path field */}
        <FormField
          label="Workspace Path"
          hint="(absolute path to a directory)"
          helper="Start typing to see directory suggestions. Use ↑↓ to navigate, Tab or Enter to select."
        >
          <div className="relative">
            <TextField
              ref={pathInputRef}
              type="text"
              placeholder="/home/user/my-project"
              value={addPath}
              onChange={(e) => handlePathChange(e.target.value)}
              onKeyDown={handlePathKeyDown}
              onFocus={() => addPath.trim() && suggestions.length > 0 && setSuggestionsOpen(true)}
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
                    <Icon name="folder_open" size={14} className="flex-shrink-0 opacity-60" />
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        </FormField>

        {/* Display name field */}
        <FormField label="Display Name" hint="(optional — defaults to folder name)">
          <TextField
            type="text"
            placeholder={addPath ? addPath.split('/').filter(Boolean).pop() || '' : 'e.g. My Project'}
            value={addName}
            onChange={(e) => setAddName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
          />
        </FormField>

        {/* Preview */}
        {addPath.trim() && (
          <div className="rounded-lg border border-[var(--border2)] bg-[var(--hover-bg)] p-4">
            <div className="text-xs font-medium text-[var(--muted)] mb-3 uppercase tracking-wider">Preview</div>
            <div className="flex items-center gap-3">
              <Icon name="folder_open" size={23} className="text-[var(--accent)] flex-shrink-0" />
              <div className="min-w-0">
                <div className="text-sm font-medium truncate">
                  {addName.trim() || addPath.trim().split('/').filter(Boolean).pop() || addPath.trim()}
                </div>
                <div className="text-xs text-[var(--muted)] truncate mt-0.5">{addPath.trim()}</div>
              </div>
            </div>
          </div>
        )}
    </FormPage>
  );
}
