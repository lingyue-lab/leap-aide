import { useTranslation } from 'react-i18next';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import type { SettingsGroup } from '@/stores/useSettingsTabStore';
import CustomScrollbar from '@/components/common/CustomScrollbar';
import { Icon } from '@/components/common/Icons';

interface GroupConfig {
  id: SettingsGroup;
  icon: React.ReactNode;
}

const GROUPS: GroupConfig[] = [
  {
    id: 'agent',
    icon: (
      <Icon name="smart_toy" size={18} />
    ),
  },
  {
    id: 'input',
    icon: (
      <Icon name="monitor" size={18} />
    ),
  },
  {
    id: 'providers',
    icon: (
      <Icon name="lock" size={18} />
    ),
  },
  {
    id: 'sessions',
    icon: (
      <Icon name="format_list_bulleted" size={18} />
    ),
  },
  {
    id: 'history',
    icon: (
      <Icon name="history" size={18} />
    ),
  },
  {
    id: 'config',
    icon: (
      <Icon name="description" size={18} />
    ),
  },
  {
    id: 'appearance',
    icon: (
      <Icon name="light_mode" size={18} />
    ),
  },
  {
    id: 'other',
    icon: (
      <Icon name="more_horiz" size={18} />
    ),
  },
  {
    id: 'about',
    icon: (
      <Icon name="info" size={18} />
    ),
  },
];

export default function SettingsPanel() {
  const { t } = useTranslation();
  const selectedGroup = useSettingsTabStore((s) => s.selectedGroup);
  const setSelectedGroup = useSettingsTabStore((s) => s.setSelectedGroup);

  return (
    <div className="settings-sidebar-panel">
      {/* Header */}
      <div className="sidebar-header">
        <span className="sidebar-header-title">
          {t('settings.title')}
        </span>
      </div>

      {/* Groups list */}
      <CustomScrollbar className="flex-1">
        {GROUPS.map((group) => {
          const isSelected = selectedGroup === group.id;
          const label = t(`settings.groups.${group.id}`);
          const description = t(`settings.groups.${group.id}_desc`);
          return (
            <div
              key={group.id}
              className={`sidebar-item${isSelected ? ' active' : ''}`}
              onClick={() => setSelectedGroup(group.id)}
            >
              <div className="sidebar-item-text">
                <div className="sidebar-item-title-row">
                  <span className="sidebar-item-title">{label}</span>
                </div>
                <div className="text-sm text-[var(--muted)]">{description}</div>
              </div>
              {isSelected && (
                <span style={{ marginLeft: 'auto', color: 'var(--blue)', flexShrink: 0 }}>
                  <Icon name="chevron_right" size={16} />
                </span>
              )}
            </div>
          );
        })}
      </CustomScrollbar>
    </div>
  );
}
