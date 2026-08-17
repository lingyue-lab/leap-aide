import { useEffect, useRef } from 'react';
import { Icon } from '@/components/common/Icons';

interface SessionSearchModalProps {
  open: boolean;
  onClose: () => void;
  placeholder: string;
  value: string;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  /** Content rendered below the search bar: info line, results list, empty state, etc. */
  children?: React.ReactNode;
}

/**
 * Shared "DeepSeek-style" centred command palette used by SessionList and SkillsPanel.
 * Handles the overlay, search bar, Escape-to-close and auto-focus; parents own the
 * query state and render their own result rows via `children`.
 */
export default function SessionSearchModal({
  open,
  onClose,
  placeholder,
  value,
  onChange,
  children,
}: SessionSearchModalProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  // Close on Escape even when the input is not focused
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  // Auto-focus the input when the modal opens
  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(timer);
  }, [open]);

  if (!open) return null;

  return (
    <div className="session-search-overlay" onClick={onClose}>
      <div className="session-search-modal" onClick={(e) => e.stopPropagation()}>
        <div className="session-search-bar">
          <Icon name="search" size={18} className="session-search-bar-icon" />
          <input
            ref={inputRef}
            type="text"
            className="no-focus-ring"
            placeholder={placeholder}
            value={value}
            onChange={onChange}
            onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
          />
          <span className="session-search-bar-shortcut">ESC</span>
        </div>
        {children}
      </div>
    </div>
  );
}
