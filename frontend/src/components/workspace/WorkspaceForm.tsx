import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import FormPage, { FormActions } from '@/components/common/FormPage';
import { FormField, TextField } from '@/components/common/FormField';
import { Icon } from '@/components/common/Icons';

interface WorkspaceFormProps {
  onSuccess: () => void;
  onCancel: () => void;
}

const MENU_MAX_H = 280;

export default function WorkspaceForm({ onSuccess, onCancel }: WorkspaceFormProps) {
  const { t } = useTranslation('common');
  const { addWorkspace } = useWorkspaceStore();

  const [addPath, setAddPath] = useState('');
  const [addName, setAddName] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Directory autocomplete: an M3 menu anchored under the path input.
  // Rendered through a portal with position:fixed so the form's scroll
  // container (FormPage/CustomScrollbar, overflow:hidden/auto) cannot clip it
  // (issue #22). The menu only opens when there is something to show.
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const [activeSugIdx, setActiveSugIdx] = useState(-1);
  const [menuStyle, setMenuStyle] = useState<React.CSSProperties>({});
  const suggestTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pathInputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    pathInputRef.current?.focus();
  }, []);

  const positionMenu = useCallback(() => {
    const el = pathInputRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const gap = 4;
    const spaceBelow = window.innerHeight - r.bottom - gap - 8;
    const spaceAbove = r.top - gap - 8;
    const openUp = spaceBelow < Math.min(MENU_MAX_H, 160) && spaceAbove > spaceBelow;
    setMenuStyle({
      position: 'fixed',
      left: r.left,
      width: r.width,
      top: openUp ? undefined : r.bottom + gap,
      bottom: openUp ? window.innerHeight - r.top + gap : undefined,
      maxHeight: Math.max(120, Math.min(MENU_MAX_H, openUp ? spaceAbove : spaceBelow)),
      zIndex: 1000,
    });
  }, []);

  const fetchSuggestions = useCallback(async (prefix: string) => {
    try {
      const res = await fetch(
        `/api/workspaces/suggest?prefix=${encodeURIComponent(prefix)}`,
        { credentials: 'include' },
      );
      if (!res.ok) return;
      const data = await res.json();
      const list: string[] = data.suggestions || [];
      setSuggestions(list);
      setActiveSugIdx(-1);
      if (list.length > 0) {
        positionMenu();
        setMenuOpen(true);
      } else {
        setMenuOpen(false);
      }
    } catch {
      /* silent */
    }
  }, [positionMenu]);

  const handlePathChange = (value: string) => {
    setAddPath(value);
    if (suggestTimer.current) clearTimeout(suggestTimer.current);
    if (!value.trim()) {
      setSuggestions([]);
      setMenuOpen(false);
      return;
    }
    suggestTimer.current = setTimeout(() => fetchSuggestions(value), 200);
  };

  const handleSelectSuggestion = (path: string) => {
    const withSlash = path.endsWith('/') ? path : path + '/';
    setAddPath(withSlash);
    setActiveSugIdx(-1);
    setMenuOpen(false);
    fetchSuggestions(withSlash);
    pathInputRef.current?.focus();
  };

  // Close on outside mousedown (input and menu excluded).
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target)) return;
      if (pathInputRef.current?.contains(target)) return;
      setMenuOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [menuOpen]);

  // Close on scroll/resize: a fixed menu would otherwise detach from the input.
  useEffect(() => {
    if (!menuOpen) return;
    const close = () => setMenuOpen(false);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [menuOpen]);

  const handlePathKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!menuOpen || suggestions.length === 0) {
      if (e.key === 'Enter') { e.preventDefault(); handleSubmit(); }
      if (e.key === 'Escape') setMenuOpen(false);
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
        setMenuOpen(false);
        handleSubmit();
      }
    } else if (e.key === 'Tab') {
      e.preventDefault();
      if (activeSugIdx >= 0 && suggestions[activeSugIdx]) {
        handleSelectSuggestion(suggestions[activeSugIdx]);
      } else if (suggestions.length === 1) {
        handleSelectSuggestion(suggestions[0]);
      }
    } else if (e.key === 'Escape') {
      setMenuOpen(false);
    }
  };

  const handleSubmit = async () => {
    const path = addPath.trim();
    if (!path) {
      setError(t('workspaces.form.error_path_required'));
      return;
    }
    setMenuOpen(false);
    setSubmitting(true);
    setError('');
    try {
      await addWorkspace(path, addName.trim() || undefined);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('workspaces.form.error_add_failed'));
    } finally {
      setSubmitting(false);
    }
  };

  const menu =
    menuOpen && suggestions.length > 0 && typeof document !== 'undefined'
      ? createPortal(
          <div
            ref={menuRef}
            role="listbox"
            aria-label={t('workspaces.form.suggestions_title')}
            style={menuStyle}
            className="rounded-xl border border-[var(--border)] bg-[var(--surface)] shadow-xl overflow-y-auto py-1"
          >
            {suggestions.map((s, i) => (
              <button
                key={s}
                type="button"
                role="option"
                aria-selected={i === activeSugIdx}
                onMouseDown={(e) => { e.preventDefault(); handleSelectSuggestion(s); }}
                className={`w-full text-left px-3 py-2 text-sm truncate flex items-center gap-2 ${
                  i === activeSugIdx
                    ? 'bg-[var(--hover-bg)] text-[var(--text)]'
                    : 'text-[var(--muted)] hover:bg-[var(--hover-bg)] hover:text-[var(--text)]'
                }`}
              >
                <Icon name="folder_open" size={16} className="flex-shrink-0 opacity-60" />
                <span className="truncate">{s}</span>
              </button>
            ))}
          </div>,
          document.body,
        )
      : null;

  return (
    <>
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
        {/* Path field — the suggestion menu is anchored to this input */}
        <FormField
          label={t('workspaces.form.path_label')}
          hint={t('workspaces.form.path_hint')}
          helper={t('workspaces.form.path_helper')}
        >
          <TextField
            ref={pathInputRef}
            type="text"
            placeholder={t('workspaces.form.path_placeholder')}
            value={addPath}
            onChange={(e) => handlePathChange(e.target.value)}
            onKeyDown={handlePathKeyDown}
            onFocus={() => { if (addPath.trim()) fetchSuggestions(addPath); }}
          />
        </FormField>

        {/* Display name field */}
        <FormField
          label={t('workspaces.form.name_label')}
          hint={t('workspaces.form.name_hint')}
        >
          <TextField
            type="text"
            placeholder={addPath ? addPath.split('/').filter(Boolean).pop() || '' : t('workspaces.form.name_placeholder')}
            value={addName}
            onChange={(e) => setAddName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
          />
        </FormField>
      </FormPage>
      {menu}
    </>
  );
}
