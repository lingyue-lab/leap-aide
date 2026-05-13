import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSkillStore } from '@/stores/useSkillStore';
import type { Skill } from '@/types';

export default function SkillsPanel() {
  const { t } = useTranslation('common');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});

  const selectedSkill = useSkillStore((s) => s.selectedSkill);
  const selectSkill = useSkillStore((s) => s.selectSkill);
  const clearSelection = useSkillStore((s) => s.clearSelection);
  const cancelEditing = useSkillStore((s) => s.cancelEditing);
  const skillsTabActive = useSkillStore((s) => s.skillsTabActive);
  const storeSkills = useSkillStore((s) => s.skills);
  const loadSkillsFromStore = useSkillStore((s) => s.loadSkills);
  const setShowNewSkillForm = useSkillStore((s) => s.setShowNewSkillForm);

  useEffect(() => {
    loadSkills();
  }, []);

  useEffect(() => {
    if (storeSkills.length > 0 && !selectedSkill) {
      selectSkill(storeSkills[0]);
    }
  }, [storeSkills]);

  useEffect(() => {
    if (skillsTabActive && storeSkills.length > 0) {
      const firstSkill = storeSkills[0];
      const firstCat = firstSkill.category || 'Uncategorized';
      // Collapse all groups, only expand the one containing the first skill
      const allCats = Array.from(new Set(storeSkills.map((s) => s.category || 'Uncategorized')));
      const collapsed: Record<string, boolean> = {};
      for (const cat of allCats) {
        collapsed[cat] = cat !== firstCat;
      }
      setCollapsedGroups(collapsed);
      if (!selectedSkill) {
        selectSkill(firstSkill);
      }
    }
  }, [skillsTabActive]);

  const loadSkills = async () => {
    try {
      setLoading(true);
      await loadSkillsFromStore();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load skills');
    } finally {
      setLoading(false);
    }
  };

  const skills = storeSkills;
  const filteredSkills = skills.filter((skill) => {
    if (!searchQuery.trim()) return true;
    const query = searchQuery.toLowerCase();
    return (
      skill.name.toLowerCase().includes(query) ||
      (skill.label && skill.label.toLowerCase().includes(query)) ||
      (skill.category && skill.category.toLowerCase().includes(query)) ||
      (skill.description && skill.description.toLowerCase().includes(query))
    );
  });

  // Group skills by category
  const groupedSkills: Record<string, Skill[]> = {};
  for (const skill of filteredSkills) {
    const cat = skill.category || 'Uncategorized';
    if (!groupedSkills[cat]) groupedSkills[cat] = [];
    groupedSkills[cat].push(skill);
  }
  const sortedCategories = Object.keys(groupedSkills).sort((a, b) => {
    if (a === 'Uncategorized') return 1;
    if (b === 'Uncategorized') return -1;
    return a.localeCompare(b);
  });

  const toggleGroup = (cat: string) => {
    setCollapsedGroups((prev) => ({ ...prev, [cat]: !prev[cat] }));
  };

  const renderSkillGroups = () => {
    return sortedCategories.map((cat) => (
      <div key={cat} className="skill-group">
        {sortedCategories.length > 1 && (
          <div
            className="skill-group-header flex items-center gap-1.5 px-2 py-1.5 text-xs font-semibold cursor-pointer select-none sticky top-0 z-10"
            style={{
              color: 'var(--foreground)',
              background: 'color-mix(in srgb, var(--border) 80%, transparent)',
              borderBottom: '1px solid var(--border)',
              borderTop: '1px solid var(--border2)',
            }}
            onClick={() => toggleGroup(cat)}
          >
            <svg
              width="10" height="10" viewBox="0 0 10 6"
              className={`transition-transform flex-shrink-0 ${collapsedGroups[cat] ? '-rotate-90' : ''}`}
              style={{ opacity: 0.7 }}
            >
              <path d="M1 1 L5 5 L9 1" fill="none" stroke="currentColor" strokeWidth="1.5" />
            </svg>
            <span className="uppercase tracking-wider">{cat}</span>
            <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--border)] text-[var(--muted)]">
              {groupedSkills[cat].length}
            </span>
          </div>
        )}
        {!collapsedGroups[cat] && groupedSkills[cat].map((skill) => {
          const isSelected = selectedSkill?.name === skill.name;
          return (
            <div
              key={skill.name}
              className={`skill-item p-2 pl-4 border-b border-[var(--border2)] cursor-pointer transition-colors ${
                isSelected
                  ? 'bg-[var(--hover-bg)]'
                  : 'hover:bg-[color-mix(in_srgb,var(--hover-bg)_50%,transparent)]'
              }`}
              onClick={() => { cancelEditing(); selectSkill(skill); }}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="skill-name text-sm font-medium">
                    {skill.label || skill.name}
                  </span>
                  {skill.category && sortedCategories.length === 1 && (
                    <span className="text-xs text-[var(--muted)] bg-[var(--border)] px-1.5 py-0.5 rounded">
                      {skill.category}
                    </span>
                  )}
                </div>
                {skill.description && (
                  <div className="text-xs text-[var(--muted)] mt-1 line-clamp-2">
                    {skill.description}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    ));
  };

  if (loading) {
    return (
      <div className="p-3 text-center text-[var(--muted)] text-sm">
        {t('common.loading') || 'Loading...'}
      </div>
    );
  }

  return (
    <div className="skills-panel">
      {/* Header */}
      <div className="flex items-center gap-2 p-2 border-b border-[var(--border)]">
        <input
          type="text"
          placeholder={t('skills.search') || 'Search skills...'}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="flex-1 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
        />
        <button
          className="cron-btn run text-xs px-2 py-1"
          onClick={() => { cancelEditing(); clearSelection(); setShowNewSkillForm(true); }}
        >
          +
        </button>
      </div>

      {/* Skills List */}
      <div className="skills-list flex-1 overflow-y-auto">
        {error && (
          <div className="p-2 text-sm text-[var(--accent)]">{error}</div>
        )}
        {filteredSkills.length === 0 && !error && (
          <div className="p-3 text-sm text-[var(--muted)] text-center italic">
            {searchQuery ? (t('skills.no_results') || 'No matching skills') : (t('skills.empty') || 'No skills')}
          </div>
        )}
        {renderSkillGroups()}
      </div>
    </div>
  );
}