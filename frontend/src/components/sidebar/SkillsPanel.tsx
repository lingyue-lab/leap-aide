import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { skillsService } from '@/services/api';
import type { Skill } from '@/types';

export default function SkillsPanel() {
  const { t } = useTranslation('common');
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [formError, setFormError] = useState('');

  // Form state
  const [formName, setFormName] = useState('');
  const [formCategory, setFormCategory] = useState('');
  const [formContent, setFormContent] = useState('');

  useEffect(() => {
    loadSkills();
  }, []);

  const loadSkills = async () => {
    try {
      setLoading(true);
      const data = await skillsService.list();
      setSkills(data.skills);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load skills');
    } finally {
      setLoading(false);
    }
  };

  const filteredSkills = skills.filter((skill) => {
    if (!searchQuery.trim()) return true;
    const query = searchQuery.toLowerCase();
    return (
      skill.name.toLowerCase().includes(query) ||
      skill.label.toLowerCase().includes(query) ||
      (skill.category && skill.category.toLowerCase().includes(query)) ||
      (skill.description && skill.description.toLowerCase().includes(query))
    );
  });

  const toggleForm = (skill?: Skill) => {
    if (skill) {
      setEditingSkill(skill);
      setFormName(skill.name);
      setFormCategory(skill.category || '');
      setFormContent(skill.content || '');
    } else {
      setEditingSkill(null);
      setFormName('');
      setFormCategory('');
      setFormContent('');
    }
    setShowForm(!showForm);
    setFormError('');
  };

  const handleSubmit = async () => {
    if (!formName.trim()) {
      setFormError('Skill name is required');
      return;
    }
    if (!formContent.trim()) {
      setFormError('Skill content is required');
      return;
    }

    try {
      await skillsService.save(formName.trim(), formContent, formCategory.trim() || undefined);
      loadSkills();
      toggleForm();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Failed to save skill');
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(t('skills.confirm_delete') || 'Delete this skill?')) return;
    try {
      await skillsService.delete(name);
      loadSkills();
    } catch (err) {
      console.error('Failed to delete skill:', err);
    }
  };

  const handleEdit = async (skill: Skill) => {
    try {
      const data = await skillsService.getContent(skill.name);
      setEditingSkill(skill);
      setFormName(skill.name);
      setFormCategory(skill.category || '');
      setFormContent(data.content);
      setShowForm(true);
    } catch (err) {
      console.error('Failed to load skill content:', err);
    }
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
          onClick={() => toggleForm()}
        >
          + {t('skills.new') || 'New Skill'}
        </button>
      </div>

      {/* Create/Edit Form */}
      {showForm && (
        <div className="skill-form p-2 border-b border-[var(--border)]">
          <input
            type="text"
            placeholder="Skill name (e.g. my-skill)"
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
            disabled={!!editingSkill}
          />
          <input
            type="text"
            placeholder="Category (optional, e.g. devops)"
            value={formCategory}
            onChange={(e) => setFormCategory(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded"
          />
          <textarea
            rows={6}
            placeholder="SKILL.md content (YAML frontmatter + markdown body)"
            value={formContent}
            onChange={(e) => setFormContent(e.target.value)}
            className="w-full mb-2 px-2 py-1 text-sm bg-[rgba(255,255,255,0.05)] border border-[var(--border2)] rounded font-mono text-xs resize-vertical"
          />
          <div className="flex gap-2">
            <button
              className="cron-btn run flex-1 text-xs py-1"
              onClick={handleSubmit}
            >
              {editingSkill ? (t('skills.save') || 'Save') : (t('skills.create') || 'Create')}
            </button>
            <button
              className="cron-btn flex-1 text-xs py-1"
              onClick={() => toggleForm()}
            >
              {t('common.cancel') || 'Cancel'}
            </button>
          </div>
          {formError && (
            <div className="text-xs text-[var(--accent)] mt-2">{formError}</div>
          )}
        </div>
      )}

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
        {filteredSkills.map((skill) => (
          <div
            key={skill.name}
            className="skill-item p-2 border-b border-[var(--border2)] hover:bg-[var(--hover-bg)]"
          >
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="skill-name text-sm font-medium">
                    {skill.label || skill.name}
                  </span>
                  {skill.category && (
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
              <div className="flex items-center gap-1 ml-2 flex-shrink-0">
                <button
                  className="icon-btn"
                  onClick={() => handleEdit(skill)}
                  title="Edit"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                  </svg>
                </button>
                <button
                  className="icon-btn text-[var(--accent)]"
                  onClick={() => handleDelete(skill.name)}
                  title="Delete"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
