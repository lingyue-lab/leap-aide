import { useTranslation } from 'react-i18next';

export default function ThinkingIndicator() {
  const { t } = useTranslation('chat');

  return (
    <div className="thinking">
      <span className="thinking-dot" />
      <span className="thinking-dot" />
      <span className="thinking-dot" />
      <span className="ml-2">{t('thinking')}</span>
    </div>
  );
}
