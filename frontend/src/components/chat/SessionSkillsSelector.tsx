import { useSessionStore } from '@/stores/useSessionStore';
import { useTranslation } from 'react-i18next';
import { Icon } from '@/components/common/Icons';

interface SessionSkillsSelectorProps {
  onClick: () => void;
  isOpen: boolean;
}

export default function SessionSkillsSelector({ onClick, isOpen }: SessionSkillsSelectorProps) {
  const { t } = useTranslation('common');
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const sessions = useSessionStore((s) => s.sessions);
  const currentSession = useSessionStore((s) => s.currentSession);

  // Prefer skills from the sessions list (updated immediately on selectSession)
  // Fall back to currentSession (set after loadSession completes)
  const activeItem = sessions.find((s) => s.session_id === currentSessionId);
  const sessionSkills: string[] | null =
    activeItem !== undefined
      ? (activeItem.skills ?? null)
      : (currentSession?.skills ?? null);

  const isDefault = sessionSkills === null;
  const boundCount = sessionSkills?.length ?? 0;

  if (!currentSessionId) return null;

  const label = isDefault
    ? t('session_skills.default', 'Skills: All')
    : boundCount === 0
    ? t('session_skills.none', 'Skills: None')
    : t('session_skills.count', 'Skills: {{count}}', { count: boundCount });

  return (
    <button
      className={`chip workspace-toggle-btn ${isOpen || !isDefault ? 'active' : ''}`}
      onClick={onClick}
      title={t('session_skills.button_title', 'Configure skills for this session')}
      aria-expanded={isOpen}
    >
      {/* Puzzle piece icon */}
      <Icon name="extension" size={18} />
      <span className="workspace-toggle-label">{label}</span>
    </button>
  );
}
