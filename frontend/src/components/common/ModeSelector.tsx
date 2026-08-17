import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useModeStore, type ApprovalMode } from '@/stores/useModeStore';
import SingleSelection from '@/components/common/SingleSelection';
import type { SelectionOption } from '@/components/common/SingleSelection';
import { Icon } from '@/components/common/Icons';

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

  const handleSelect = useCallback(
    (value: string | null) => {
      if (!sessionId || !value) return;
      setSessionMode(sessionId, value as ApprovalMode);
    },
    [sessionId, setSessionMode]
  );

  const options: SelectionOption[] = MODES.map(({ key, i18nKey }) => ({
    value: key,
    label: t(i18nKey),
    disabled: key === 'smart' && !smartSupported,
  }));

  return (
    <SingleSelection
      value={currentMode}
      options={options}
      trigger={
        <>
          <Icon
            name={currentMode === 'yolo' ? 'bolt' : currentMode === 'smart' ? 'schedule' : 'description'}
            size={18}
          />
          <span className="mode-label">{t(`mode.${currentMode}`)}</span>
        </>
      }
      triggerLabel={`${t('mode.label')}: ${t(`mode.${currentMode}`)}`}
      triggerClass="mode-selector-btn"
      onSelect={handleSelect}
      header={t('mode.label')}
    />
  );
}
