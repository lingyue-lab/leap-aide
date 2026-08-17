import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useSkillStore } from '@/stores/useSkillStore';
import { useSessionStore } from '@/stores/useSessionStore';
import { skillsService } from '@/services/api';
import type { Skill } from '@/types';
import CustomScrollbar from '@/components/common/CustomScrollbar';
import SessionSearchModal from '@/components/common/SessionSearchModal';
import { TrashMultiIcon, Icon } from '@/components/common/Icons';

const isMac = typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform || '');

const UNCATEGORIZED = 'uncategorized';

export default function SkillsPanel({ isActive = false }: { isActive?: boolean }) {
  const { t } = useTranslation('common');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [showSearchModal, setShowSearchModal] = useState(false);

  const selectedSkill = useSkillStore((s) => s.selectedSkill);
  const selectSkill = useSkillStore((s) => s.selectSkill);
  const clearSelection = useSkillStore((s) => s.clearSelection);
  const cancelEditing = useSkillStore((s) => s.cancelEditing);
  const skillsTabActive = useSkillStore((s) => s.skillsTabActive);
  const storeSkills = useSkillStore((s) => s.skills);
  const loadSkillsFromStore = useSkillStore((s) => s.loadSkills);
  const setShowNewSkillForm = useSkillStore((s) => s.setShowNewSkillForm);
  const sessions = useSessionStore((s) => s.sessions);

  useEffect(() => {
    loadSkills();
  }, []);

  useEffect(() => {
    if (skillsTabActive && storeSkills.length > 0) {
      const firstSkill = storeSkills[0];
      const firstCat = firstSkill.category || UNCATEGORIZED;
      // Collapse all groups, only expand the one containing the first skill
      const allCats = Array.from(new Set(storeSkills.map((s) => s.category || UNCATEGORIZED)));
      const collapsed: Record<string, boolean> = {};
      for (const cat of allCats) {
        collapsed[cat] = cat !== firstCat;
      }
      setCollapsedGroups(collapsed);
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

  const handleDeleteSkill = async (skill: Skill) => {
    const using = sessions.filter((s) => s.skills?.includes(skill.name));
    if (using.length > 0) {
      const names = using.map((s) => `"${s.title}"`).join(', ');
      alert(`Cannot delete skill: it is used by ${using.length} session${using.length > 1 ? 's' : ''}: ${names}`);
      return;
    }
    if (!confirm(t('skills.confirm_delete') || 'Delete this skill?')) return;
    try {
      await skillsService.delete(skill.name);
      if (selectedSkill?.name === skill.name) {
        clearSelection();
      }
      await loadSkillsFromStore();
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to delete skill');
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
    const cat = skill.category || UNCATEGORIZED;
    if (!groupedSkills[cat]) groupedSkills[cat] = [];
    groupedSkills[cat].push(skill);
  }
  const sortedCategories = Object.keys(groupedSkills).sort((a, b) => {
    if (a === UNCATEGORIZED) return 1;
    if (b === UNCATEGORIZED) return -1;
    return a.localeCompare(b);
  });

  const toggleGroup = (cat: string) => {
    setCollapsedGroups((prev) => ({ ...prev, [cat]: !prev[cat] }));
  };

  const handleOpenSearch = useCallback(() => {
    setShowSearchModal(true);
  }, []);

  const handleCloseSearch = useCallback(() => {
    setShowSearchModal(false);
    setSearchQuery('');
  }, []);

  // Select a skill from search results and close the palette
  const handleSelectSkill = useCallback((skill: Skill) => {
    cancelEditing();
    selectSkill(skill);
    handleCloseSearch();
  }, [cancelEditing, selectSkill, handleCloseSearch]);

  // Keyboard shortcut: Cmd/Ctrl+F (only when skills panel is active)
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'f') {
        if (!isActive) return;  // let browser Ctrl+F work on other panels
        e.preventDefault();
        setShowSearchModal(true);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isActive]);

  const renderSkillGroups = () => {
    return sortedCategories.map((cat) => (
      <div key={cat} className="skill-group">
        {sortedCategories.length > 1 && (
          <div
            className={`sidebar-items-group-header${collapsedGroups[cat] ? ' collapsed' : ''}`}
            onClick={() => toggleGroup(cat)}
          >
            <Icon name="keyboard_arrow_down" size={14} className="sidebar-items-group-caret" />
            <span className="sidebar-items-group-label">{cat}</span>
            <span className="sidebar-items-group-count">{groupedSkills[cat].length}</span>
          </div>
        )}
        {!collapsedGroups[cat] && groupedSkills[cat].map((skill) => {
          const isSelected = selectedSkill?.name === skill.name;
          return (
            <div
              key={skill.name}
              className={`sidebar-item${isSelected ? ' active' : ''}`}
              onClick={() => { cancelEditing(); selectSkill(skill); }}
            >
              <div className="sidebar-item-text">
                <div className="sidebar-item-title-row">
                  <span className="sidebar-item-title">
                    {skill.label || skill.name}
                  </span>
                </div>
                {skill.description && (
                  <div className="text-sm text-[var(--muted)] mt-1 line-clamp-2">
                    {skill.description}
                  </div>
                )}
              </div>
              <div className="sidebar-item-actions">
                <button
                  className="sidebar-item-actions-trigger"
                  onClick={(e) => { e.stopPropagation(); handleDeleteSkill(skill); }}
                  title={t('sessions.delete') || 'Delete skill'}
                >
                  <TrashMultiIcon size={16} />
                </button>
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
      {/* Search Modal (shared command palette) */}
      <SessionSearchModal
        open={showSearchModal}
        onClose={handleCloseSearch}
        placeholder={t('skills.search') || 'Search skills...'}
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      >
        {searchQuery.trim() && filteredSkills.length > 0 && (
          <div className="session-search-results">
            {filteredSkills.map((skill) => (
              <div
                key={skill.name}
                className="sidebar-item"
                onClick={() => handleSelectSkill(skill)}
              >
                <div className="sidebar-item-text">
                  <div className="sidebar-item-title-row">
                    <span className="sidebar-item-title">{skill.label || skill.name}</span>
                  </div>
                  {skill.description && (
                    <div className="text-sm text-[var(--muted)] mt-1 line-clamp-2">
                      {skill.description}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        {searchQuery.trim() && filteredSkills.length === 0 && (
          <div className="session-search-empty">{t('skills.no_results') || 'No matching skills'}</div>
        )}
      </SessionSearchModal>

      {/* Header */}
      <div className="sidebar-header">
        <span className="sidebar-header-title">{t('tabs.skills')}</span>
        <div className="sidebar-header-actions">
          <button
            className="icon-btn"
            onClick={handleOpenSearch}
            title={`${t('skills.search') || 'Search skills...'} (${isMac ? '⌘' : 'Ctrl+'}F)`}
          >
            <Icon name="search" size={18} />
          </button>
          <button
            className="icon-btn"
            onClick={() => { cancelEditing(); clearSelection(); setShowNewSkillForm(true); }}
            title="New Skill"
          >
            <Icon name="add" size={18} />
          </button>
        </div>
      </div>

      {/* Skills List */}
      <CustomScrollbar className="flex-1">
        {error && (
          <div className="p-2 text-sm text-[var(--accent)]">{error}</div>
        )}
        {filteredSkills.length === 0 && !error && (
          <div className="p-3 text-sm text-[var(--muted)] text-center italic">
            {searchQuery ? (t('skills.no_results') || 'No matching skills') : (t('skills.empty') || 'No skills')}
          </div>
        )}
        {renderSkillGroups()}
      </CustomScrollbar>
    </div>
  );
}