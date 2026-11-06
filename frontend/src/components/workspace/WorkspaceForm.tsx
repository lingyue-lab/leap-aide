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
const MENU_ID = 'workspace-path-suggestions';
const optionId = (index: number) => `${MENU_ID}-option-${index}`;

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
  // Monotonic request id.  Only the response of the newest request may
  // paint the menu, so a slow reply for an older prefix can never replace
  // the results of the prefix the user is actually typing.
  const requestIdRef = useRef(0);
  // Latest path value.  Event handlers that outlive a render (focus) must
  // not read a stale `addPath`.
  const pathRef = useRef('');

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
    const requestId = ++requestIdRef.current;
    try {
      const res = await fetch(
        `/api/workspaces/suggest?prefix=${encodeURIComponent(prefix)}`,
        { credentials: 'include' },
      );
      // A newer request has been issued in the meantime — drop this reply.
      if (requestId !== requestIdRef.current) return;
      if (!res.ok) {
        // A failed lookup must not leave the previous list on screen.
        setSuggestions([]);
        setMenuOpen(false);
        return;
      }
      const data = await res.json();
      if (requestId !== requestIdRef.current) return;
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
      if (requestId !== requestIdRef.current) return;
      setSuggestions([]);
      setMenuOpen(false);
    }
  }, [positionMenu]);

  const handlePathChange = (value: string) => {
    setAddPath(value);
    pathRef.current = value;
    // Invalidate every in-flight response: a stale reply must neither
    // repaint the menu nor reopen it after the field was cleared.
    requestIdRef.current++;
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
    // Drop the pending debounce — it would re-query the previous prefix.
    if (suggestTimer.current) {
      clearTimeout(suggestTimer.current);
      suggestTimer.current = null;
    }
    pathRef.current = withSlash;
    setAddPath(withSlash);
    setActiveSugIdx(-1);
    setMenuOpen(false);
    pathInputRef.current?.focus();
    fetchSuggestions(withSlash);
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

  // Close on scroll/resize: a fixed menu would otherwise detach from the
  // input.  The menu scrolls itself (overflow-y: auto) — that must not close
  // it, otherwise the items past the fold become unreachable.
  useEffect(() => {
    if (!menuOpen) return;
    const onScroll = (e: Event) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      setMenuOpen(false);
    };
    const onResize = () => setMenuOpen(false);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
    };
  }, [menuOpen]);

  // Keep the highlighted option visible: the menu is capped at MENU_MAX_H,
  // so arrow-key navigation can move the highlight past the visible rows.
  useEffect(() => {
    if (activeSugIdx < 0) return;
    const opt = menuRef.current?.children[activeSugIdx] as HTMLElement | undefined;
    opt?.scrollIntoView?.({ block: 'nearest' });
  }, [activeSugIdx]);

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
      const target =
        activeSugIdx >= 0
          ? suggestions[activeSugIdx]
          : suggestions.length === 1
            ? suggestions[0]
            : null;
      // Only swallow Tab when a suggestion is actually accepted — otherwise
      // Tab must move the focus out of the field instead of trapping it.
      if (target) {
        e.preventDefault();
        handleSelectSuggestion(target);
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
    // A suggest response that is still in flight must not reopen the menu
    // while the form is being submitted.
    requestIdRef.current++;
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

  const listOpen = menuOpen && suggestions.length > 0;

  const menu =
    listOpen && typeof document !== 'undefined'
      ? createPortal(
          <div
            id={MENU_ID}
            ref={menuRef}
            role="listbox"
            aria-label={t('workspaces.form.suggestions_title')}
            style={menuStyle}
            className="rounded-xl border border-[var(--border)] bg-[var(--surface)] shadow-xl overflow-y-auto py-1"
          >
            {suggestions.map((s, i) => (
              <button
                key={s}
                id={optionId(i)}
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
            role="combobox"
            aria-expanded={listOpen}
            aria-controls={MENU_ID}
            aria-autocomplete="list"
            aria-activedescendant={
              activeSugIdx >= 0 ? optionId(activeSugIdx) : undefined
            }
            placeholder={t('workspaces.form.path_placeholder')}
            value={addPath}
            onChange={(e) => handlePathChange(e.target.value)}
            onKeyDown={handlePathKeyDown}
            onFocus={() => {
              const current = pathRef.current;
              if (current.trim()) fetchSuggestions(current);
            }}
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
