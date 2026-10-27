import React, { useState, useRef, useCallback } from 'react';
import CustomScrollbar from '@/components/common/CustomScrollbar';
import { Icon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';

// ── Types ──────────────────────────────────────────────────────────────────

export interface SelectionOption {
  value: string;
  label: string;
  description?: string;
  icon?: React.ReactNode;
  badge?: string;
  disabled?: boolean;
}

export interface OptionGroup {
  header: string;
  items: SelectionOption[];
}

export interface SingleSelectionProps {
  options?: SelectionOption[];
  optionGroups?: OptionGroup[];
  value: string | null;
  disabled?: boolean;
  trigger: React.ReactNode;
  triggerLabel: string;
  triggerClass?: string;
  onSelect: (value: string | null) => void;
  header?: string;
  showNone?: boolean;
  loading?: boolean;
  emptyText?: string;
  onOpen?: () => void;
  /** Called when the close (X) button is clicked while `loading` — lets the
   *  caller abort the in-flight request that drives the spinner (issue #16:
   *  the picker's X must cancel the models fetch, not just hide the popover). */
  onCancelLoading?: () => void;
}

// ── Component ──────────────────────────────────────────────────────────────

const POPOVER_W = 280;

const SingleSelection: React.FC<SingleSelectionProps> = ({
  options,
  optionGroups,
  value,
  disabled = false,
  trigger,
  triggerLabel,
  triggerClass = 'icon-btn',
  onSelect,
  header,
  showNone = false,
  loading = false,
  emptyText = 'No options available',
  onOpen,
  onCancelLoading,
}) => {
  const [open, setOpen] = useState(false);
  const [popoverStyle, setPopoverStyle] = useState<React.CSSProperties>({});
  const containerRef = useRef<HTMLDivElement>(null);

  const toggle = useCallback(() => {
    setOpen((v) => {
      if (!v && onOpen) onOpen();
      if (!v && containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        const maxH = Math.min(600, Math.floor(window.innerHeight * 0.5));
        setPopoverStyle({
          position: 'fixed',
          top: rect.top - 8,
          left: Math.max(8, rect.left + rect.width / 2 - POPOVER_W / 2),
          minWidth: POPOVER_W,
          maxWidth: 360,
          maxHeight: maxH,
          transform: 'translateY(-100%)',
        });
      }
      return !v;
    });
  }, [onOpen]);

  const close = useCallback(() => {
    // Closing while a load is in flight also aborts it — the spinner is
    // driven by a real network request the caller may want to cancel.
    if (loading && onCancelLoading) onCancelLoading();
    setOpen(false);
  }, [loading, onCancelLoading]);

  // ── Select handler (keep popover open after selection) ──────────────────
  const handleSelect = useCallback((v: string | null) => {
    onSelect(v);
  }, [onSelect]);

  // ── Row renderer — M3: checkmark trailing, icon leading, min-height 48dp ──
  const isActive = (v: string) => {
    if (v === value) return true;
    // When showNone is enabled, treat null/undefined as equivalent to ''
    // so that the "None" option shows a checkmark for null values.
    if (showNone && v === '' && (value === null || value === undefined)) return true;
    return false;
  };

  // Unified lightning-bolt icon for every option
  const defaultIcon = <Icon name="bolt" size={16} />;

  const renderRow = (opt: SelectionOption) => (
    <button
      key={opt.value}
      className={`ss-option${isActive(opt.value) ? ' active' : ''}`}
      onClick={() => handleSelect(opt.value)}
      disabled={opt.disabled}
    >
      <span className="ss-icon">{opt.icon || defaultIcon}</span>
      <span className="ss-label-wrap">
        <span className="ss-label">{opt.label}</span>
        {opt.description && <span className="ss-desc">{opt.description}</span>}
      </span>
      {opt.badge && <span className="ss-badge">{opt.badge}</span>}
      {isActive(opt.value) && (
        <Icon name="check" size={18} className="ss-check" />
      )}
    </button>
  );

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <div className="selection-trigger" ref={containerRef}>
      <button
        className={triggerClass}
        onClick={toggle}
        disabled={disabled}
        title={triggerLabel}
        aria-label={triggerLabel}
      >
        {trigger}
      </button>

      {open && (
        <div className="selection-popover" style={popoverStyle}>
          <div className="ss-topbar">
            {header && <div className="ss-header">{header}</div>}
            <Icon name="close" size={18} className="ss-close" onClick={close} />
          </div>
          <CustomScrollbar className="ss-scroll">
            {loading ? (
              <div className="ss-status"><LoadingSpinner mode="inline" minimal size={16} /></div>
            ) : (options && options.length === 0 && (!optionGroups || optionGroups.length === 0)) ? (
              <div className="ss-status">{emptyText}</div>
            ) : (
              <>
                {showNone && renderRow({ value: '', label: 'None' })}
                {optionGroups?.map((group) => (
                  <div key={group.header}>
                    <div className="ss-group-header">{group.header}</div>
                    {group.items.map(renderRow)}
                  </div>
                ))}
                {options?.map(renderRow)}
              </>
            )}
          </CustomScrollbar>
        </div>
      )}
    </div>
  );
};

export default SingleSelection;
