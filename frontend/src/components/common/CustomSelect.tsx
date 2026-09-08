import { useState, useRef, useEffect, type ReactNode } from 'react';
import { ChevronDownIcon } from '@/components/common/Icons';

export interface CustomSelectOption {
  value: string;
  label: string;
  description?: string;
  icon?: ReactNode;
  disabled?: boolean;
}

export interface CustomSelectProps {
  options: CustomSelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  /** Show selected option's icon in the trigger. */
  showIconInTrigger?: boolean;
  /** Custom render for each option row. */
  renderOption?: (option: CustomSelectOption, isSelected: boolean) => ReactNode;
  /** Trigger className override. */
  triggerClassName?: string;
  /** Aria label for the trigger button. */
  ariaLabel?: string;
}

/**
 * Single-select dropdown — M3 outlined text-field style.
 *
 * Replaces native `<select>` to unify visual appearance with the multi-select
 * dropdowns (Skills / Context From / Enabled Toolsets) in TaskForm. M3 spec:
 * outlined text-field trigger with leading icon slot, trailing chevron that
 * rotates 180° on open, full-width menu panel below.
 */
export default function CustomSelect({
  options,
  value,
  onChange,
  placeholder = '',
  disabled = false,
  showIconInTrigger = false,
  renderOption,
  triggerClassName,
  ariaLabel,
}: CustomSelectProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => !disabled && setOpen(!open)}
        className={`form-input flex items-center justify-between gap-2 text-left ${
          disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
        } ${triggerClassName ?? ''}`}
      >
        <span className={`flex items-center gap-2 truncate ${selected ? 'text-[var(--text)]' : 'text-[var(--muted)]'}`}>
          {showIconInTrigger && selected?.icon}
          <span className="truncate">{selected ? selected.label : placeholder}</span>
        </span>
        <ChevronDownIcon
          size={16}
          className={`flex-shrink-0 opacity-70 transition-transform duration-150 ${
            open ? 'rotate-180' : ''
          }`}
        />
      </button>
      {open && (
        <div
          role="listbox"
          className="absolute left-0 right-0 top-full mt-1 z-50 rounded-lg border border-[var(--border)] bg-[var(--sidebar-bg,#1e1e1e)] shadow-xl overflow-hidden"
          style={{ maxHeight: 280, overflowY: 'auto' }}
        >
          {options.length === 0 ? (
            <div className="px-3 py-2 text-xs text-[var(--muted)]">No options</div>
          ) : (
            options.map((opt) => {
              const isSelected = opt.value === value;
              if (renderOption) {
                return (
                  <div key={opt.value} role="option" aria-selected={isSelected}>
                    {renderOption(opt, isSelected)}
                  </div>
                );
              }
              return (
                <button
                  key={opt.value}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  disabled={opt.disabled}
                  onClick={() => {
                    if (opt.disabled) return;
                    onChange(opt.value);
                    setOpen(false);
                  }}
                  className={`w-full text-left px-3 py-1.5 text-xs transition-colors flex items-center gap-2 ${
                    opt.disabled
                      ? 'opacity-50 cursor-not-allowed'
                      : isSelected
                        ? 'bg-[var(--accent)]/10 text-[var(--accent)]'
                        : 'text-[var(--muted)] hover:bg-[var(--hover-bg)] hover:text-[var(--text)]'
                  }`}
                >
                  {opt.icon}
                  <span className="flex-1 truncate">{opt.label}</span>
                  {opt.description && (
                    <span className="text-[10px] text-[var(--muted)] opacity-60 truncate">
                      {opt.description}
                    </span>
                  )}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
