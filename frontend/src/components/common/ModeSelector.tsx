import { useState, useRef, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useModeStore, type ApprovalMode } from '@/stores/useModeStore';

interface ModeSelectorProps {
  sessionId: string | null;
}

const MODES: { key: ApprovalMode; i18nKey: string }[] = [
  { key: 'manual', i18nKey: 'mode.manual' },
  { key: 'smart', i18nKey: 'mode.smart' },
  { key: 'yolo', i18nKey: 'mode.yolo' },
];

export default function ModeSelector({ sessionId }: ModeSelectorProps) {
  const { t } = useTranslation('chat');
  const { currentMode, smartSupported, setSessionMode } = useModeStore();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Dismiss on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const handleSelect = useCallback(
    (mode: ApprovalMode) => {
      if (!sessionId) return;
      setSessionMode(sessionId, mode);
      setOpen(false);
    },
    [sessionId, setSessionMode]
  );

  const modeClass = `mode-selector-btn ${currentMode}`;

  return (
    <div className="mode-selector" ref={containerRef}>
      <button
        className={modeClass}
        onClick={() => setOpen((v) => !v)}
        title={t('mode.label')}
        aria-label={t('mode.label')}
      >
        <span className="mode-label">{t(`mode.${currentMode}`)}</span>
        <svg width="8" height="5" viewBox="0 0 8 5" fill="none" style={{ marginLeft: 4, opacity: 0.6 }}>
          <path d="M1 1L4 4L7 1" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>

      {open && (
        <div className="mode-menu">
          {MODES.map(({ key, i18nKey }) => {
            const disabled = key === 'smart' && !smartSupported;
            const itemClass = `mode-menu-item ${key}${currentMode === key ? ' active' : ''}${disabled ? ' disabled' : ''}`;
            return (
              <button
                key={key}
                className={itemClass}
                onClick={() => !disabled && handleSelect(key)}
                disabled={disabled}
                title={disabled ? t('mode.smart_unsupported') : undefined}
              >
                <span>{t(i18nKey)}</span>
                {currentMode === key && (
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
