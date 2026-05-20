import { useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useSessionStore } from '@/stores/useSessionStore';
import { useSkillStore } from '@/stores/useSkillStore';
import type { Skill } from '@/types';

interface SkillsDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

// Group skills by category
function groupByCategory(skills: Skill[]): Record<string, Skill[]> {
  const groups: Record<string, Skill[]> = {};
  for (const skill of skills) {
    const cat = skill.category || 'General';
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(skill);
  }
  return groups;
}

function CheckIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export default function SkillsDrawer({ isOpen, onClose }: SkillsDrawerProps) {
  const { t } = useTranslation('common');
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const sessions = useSessionStore((s) => s.sessions);
  const currentSession = useSessionStore((s) => s.currentSession);
  const updateSessionSkills = useSessionStore((s) => s.updateSessionSkills);

  // Use sessions list (updated immediately on selectSession) as source of truth for skills,
  // fall back to currentSession (set after loadSession completes)
  const activeItem = sessions.find((s) => s.session_id === currentSessionId);
  const sessionSkills: string[] | null =
    activeItem !== undefined
      ? (activeItem.skills ?? null)
      : (currentSession?.skills ?? null);
  const activeSessionId = currentSessionId ?? currentSession?.session_id;
  const skills = useSkillStore((s) => s.skills);
  const loadSkills = useSkillStore((s) => s.loadSkills);

  // Load skills on first open
  useEffect(() => {
    if (isOpen && skills.length === 0) {
      loadSkills();
    }
  }, [isOpen]);

  const isDefault = sessionSkills === null;

  const isChecked = useCallback(
    (dirName: string) => {
      if (isDefault) return true; // default = all enabled
      return sessionSkills!.includes(dirName);
    },
    [isDefault, sessionSkills]
  );

  const toggleSkill = useCallback(
    async (dirName: string) => {
      if (!activeSessionId) return;
      let next: string[];
      if (isDefault) {
        // Switch from default (all) mode: uncheck this one = all others enabled
        next = skills
          .map((s) => s.dir_name || s.name)
          .filter((k) => k !== dirName);
      } else if (sessionSkills!.includes(dirName)) {
        next = sessionSkills!.filter((k) => k !== dirName);
      } else {
        next = [...sessionSkills!, dirName];
      }
      await updateSessionSkills(activeSessionId, next);
    },
    [activeSessionId, isDefault, sessionSkills, skills, updateSessionSkills]  );

  const resetToAll = useCallback(async () => {
    if (!activeSessionId) return;
    await updateSessionSkills(activeSessionId, null);
  }, [activeSessionId, updateSessionSkills]);

  const grouped = groupByCategory(skills);
  const categories = Object.keys(grouped).sort();

  const checkedCount = isDefault
    ? skills.length
    : (sessionSkills?.length ?? 0);

  return (
    <aside className={`right-panel ${isOpen ? 'open' : ''}`}>
      <div className="resize-handle" />

      {/* Header */}
      <div className="panel-header">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M20.24 12.24a6 6 0 0 0-8.49-8.49L5 10.5V19h8.5z" />
          <line x1="16" y1="8" x2="2" y2="22" />
          <line x1="17.5" y1="15" x2="9" y2="15" />
        </svg>
        <span className="font-semibold">{t('session_skills.panel_title', 'Session Skills')}</span>
        {skills.length > 0 && (
          <span className="text-xs tabular-nums" style={{ color: 'var(--muted)', opacity: 0.8 }}>
            {checkedCount}/{skills.length}
          </span>
        )}
        <div className="panel-actions">
          <button
            className="panel-icon-btn"
            onClick={onClose}
            title={t('session_skills.hide_panel', 'Hide skills panel')}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
        </div>
      </div>

      {/* Reset to default row */}
      <div className="px-3 py-2" style={{ borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <button
          className="flex items-center gap-2 text-xs w-full rounded px-2 py-1.5 transition-colors"
          style={{
            color: isDefault ? 'var(--accent, #6366f1)' : 'var(--muted)',
            background: isDefault ? 'var(--accent-subtle, rgba(99,102,241,.08))' : 'transparent',
          }}
          onClick={resetToAll}
          title={t('session_skills.use_all', 'Use all globally installed skills')}
        >
          <span
            className="flex-shrink-0 w-4 h-4 rounded flex items-center justify-center"
            style={{
              border: isDefault ? 'none' : '1.5px solid var(--border)',
              background: isDefault ? 'var(--accent, #6366f1)' : 'transparent',
              color: '#fff',
            }}
          >
            {isDefault && <CheckIcon />}
          </span>
          <span className={isDefault ? 'font-medium' : ''}>
            {t('session_skills.use_all', 'Default — all skills')}
          </span>
        </button>
      </div>

      {/* Scrollable skill list */}
      <div className="flex-1 overflow-y-auto p-2">
        {skills.length === 0 ? (
          <p className="text-sm italic text-center py-8" style={{ color: 'var(--muted)' }}>
            {t('session_skills.no_skills', 'No skills installed')}
          </p>
        ) : (
          categories.map((cat) => (
            <div key={cat} className="mb-3">
              {/* Category heading */}
              <div
                className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider"
                style={{ color: 'var(--muted)', letterSpacing: '0.08em' }}
              >
                {cat}
              </div>

              {grouped[cat].map((skill) => {
                const key = skill.dir_name || skill.name;
                const checked = isChecked(key);
                return (
                  <button
                    key={key}
                    className="flex items-start gap-2.5 w-full rounded-lg px-2 py-2 text-left transition-colors"
                    style={{ background: checked ? 'var(--hover-bg, rgba(0,0,0,.04))' : 'transparent' }}
                    onMouseEnter={(e) => {
                      (e.currentTarget as HTMLElement).style.background = 'var(--hover-bg, rgba(0,0,0,.06))';
                    }}
                    onMouseLeave={(e) => {
                      (e.currentTarget as HTMLElement).style.background = checked ? 'var(--hover-bg, rgba(0,0,0,.04))' : 'transparent';
                    }}
                    onClick={() => toggleSkill(key)}
                  >
                    {/* Checkbox */}
                    <span
                      className="flex-shrink-0 mt-0.5 w-4 h-4 rounded flex items-center justify-center"
                      style={{
                        border: checked ? 'none' : '1.5px solid var(--border)',
                        background: checked ? 'var(--accent, #6366f1)' : 'transparent',
                        color: '#fff',
                        minWidth: '16px',
                      }}
                    >
                      {checked && <CheckIcon />}
                    </span>

                    {/* Text */}
                    <span className="flex-1 min-w-0">
                      <span
                        className="block text-sm leading-snug"
                        style={{
                          color: checked ? 'var(--text)' : 'var(--muted)',
                          fontWeight: checked ? 500 : 400,
                        }}
                      >
                        {skill.label || skill.name}
                      </span>
                      {skill.description && (
                        <span
                          className="block text-xs leading-snug mt-0.5"
                          style={{ color: 'var(--muted)', opacity: 0.75 }}
                        >
                          {skill.description}
                        </span>
                      )}
                    </span>
                  </button>
                );
              })}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
