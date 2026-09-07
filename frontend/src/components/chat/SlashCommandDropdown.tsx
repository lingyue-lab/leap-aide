import { useTranslation } from 'react-i18next';
import type { SlashCommand } from '@/types';
import type { GroupedCommand } from '@/hooks/useSlashCommands';

export interface SlashCommandItem extends SlashCommand {
  displayName?: string;
}

interface SlashCommandDropdownProps {
  groups: GroupedCommand[];
  selectedIndex: number;
  onSelect: (cmd: SlashCommandItem) => void;
  onMouseEnterIndex: (index: number) => void;
}

export default function SlashCommandDropdown({
  groups,
  selectedIndex,
  onSelect,
  onMouseEnterIndex,
}: SlashCommandDropdownProps) {
  const { t } = useTranslation('common');
  let flatIndex = 0;

  return (
    <div
      className="slash-command-dropdown"
      style={{
        position: 'absolute',
        bottom: 'calc(100% - 12px)',
        left: 0,
        minWidth: 280,
        maxWidth: 400,
        maxHeight: 320,
        overflowY: 'auto',
        background: 'var(--surface)',
        border: '1px solid var(--border2)',
        borderRadius: 10,
        boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
        zIndex: 200,
        padding: '6px 0',
      }}
    >
      {groups.map((group) => (
        <div key={group.category}>
          {/* Category header */}
          <div
            style={{
              padding: '4px 14px 2px',
              fontSize: 11,
              fontWeight: 600,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
              color: 'var(--muted)',
            }}
          >
            {group.category}
          </div>
          {group.commands.map((cmd) => {
            const idx = flatIndex++;
            const isActive = idx === selectedIndex;
            const displayName = (cmd as SlashCommandItem).displayName || cmd.name;
            return (
              <button
                key={`${cmd.type}:${cmd.name}:${cmd.category || ''}`}
                onClick={() => onSelect(cmd as SlashCommandItem)}
                onMouseEnter={() => onMouseEnterIndex(idx)}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  width: '100%',
                  padding: '6px 14px',
                  background: isActive
                    ? 'color-mix(in srgb, var(--blue) 15%, transparent)'
                    : 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  fontSize: 14,
                  color: isActive ? 'var(--blue)' : 'var(--text)',
                  textAlign: 'left' as const,
                }}
              >
                <span
                  style={{
                    fontWeight: 600,
                    color: 'var(--blue)',
                    fontFamily: 'monospace',
                    fontSize: 14,
                    whiteSpace: 'nowrap',
                    flexShrink: 0,
                  }}
                >
                  /{displayName}{cmd.args_hint ? ` ${cmd.args_hint}` : ''}
                </span>
                <span
                  style={{
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    color: 'var(--muted)',
                    fontSize: 13,
                  }}
                >
                  {cmd.description}
                </span>
              </button>
            );
          })}
        </div>
      ))}
      {groups.length === 0 && (
        <div style={{ padding: '12px 16px', fontSize: 14, color: 'var(--muted)' }}>
          {t('common.no_matching_commands')}
        </div>
      )}
    </div>
  );
}
