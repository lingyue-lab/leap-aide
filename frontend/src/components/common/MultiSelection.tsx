import React, { useState, useRef, useCallback } from 'react';
import CustomScrollbar from '@/components/common/CustomScrollbar';
import { Icon } from '@/components/common/Icons';
import LoadingSpinner from '@/components/common/LoadingSpinner';

// ── Types ──────────────────────────────────────────────────────────────────

export interface MultiSelectionOption {
  value: string;
  label: string;
  description?: string;
  icon?: React.ReactNode;
  disabled?: boolean;
}

export interface OptionGroup {
  header: string;
  items: MultiSelectionOption[];
}

export interface MultiSelectionProps {
  options?: MultiSelectionOption[];
  optionGroups?: OptionGroup[];
  selectedValues: string[];
  onChange: (selected: string[]) => void;
  trigger: React.ReactNode;
  triggerLabel: string;
  triggerClass?: string;
  header?: string;
  loading?: boolean;
  emptyText?: string;
  onOpen?: () => void;
}

// ── Component ──────────────────────────────────────────────────────────────

const POPOVER_W = 280;

const MultiSelection: React.FC<MultiSelectionProps> = ({
  options,
  optionGroups,
  selectedValues,
  onChange,
  trigger,
  triggerLabel,
  triggerClass = 'icon-btn',
  header,
  loading = false,
  emptyText = 'No options available',
  onOpen,
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

  const close = useCallback(() => setOpen(false), []);

  // ── Toggle handler (keep popover open) ─────────────────────────────────
  const handleToggle = useCallback((v: string) => {
    const next = selectedValues.includes(v)
      ? selectedValues.filter(x => x !== v)
      : [...selectedValues, v];
    onChange(next);
  }, [selectedValues, onChange]);

  // ── Row renderer — same layout as SingleSelection, checkbox trailing ────
  const defaultIcon = <Icon name="bolt" size={16} />;

  const renderRow = (opt: MultiSelectionOption) => {
    const isChecked = selectedValues.includes(opt.value);
    return (
      <button
        key={opt.value}
        className={`ss-option${isChecked ? ' active' : ''}`}
        onClick={() => handleToggle(opt.value)}
        disabled={opt.disabled}
      >
        <span className="ss-icon">{opt.icon || defaultIcon}</span>
        <span className="ss-label-wrap">
          <span className="ss-label">{opt.label}</span>
          {opt.description && <span className="ss-desc">{opt.description}</span>}
        </span>
        {isChecked && (
          <Icon name="check" size={18} className="ss-check" />
        )}
      </button>
    );
  };

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <div className="selection-trigger" ref={containerRef}>
      <button
        className={triggerClass}
        onClick={toggle}
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

export default MultiSelection;
