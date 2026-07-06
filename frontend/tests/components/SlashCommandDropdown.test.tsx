import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import SlashCommandDropdown from '@/components/chat/SlashCommandDropdown';
import type { GroupedCommand } from '@/hooks/useSlashCommands';

const mockGroups: GroupedCommand[] = [
  {
    category: 'Session',
    commands: [
      { name: 'help', description: 'Show available commands', type: 'builtin', category: 'Session' },
      { name: 'clear', description: 'Clear chat history', type: 'builtin', category: 'Session' },
    ],
  },
  {
    category: 'software-development',
    commands: [
      { name: 'simplify-code', description: 'Clean up recent code changes', type: 'skill', category: 'software-development', dir_name: 'simplify-code' },
    ],
  },
];

describe('SlashCommandDropdown', () => {
  it('renders category headers', () => {
    render(
      <SlashCommandDropdown
        groups={mockGroups}
        selectedIndex={0}
        onSelect={() => {}}
        onMouseEnterIndex={() => {}}
      />
    );
    expect(screen.getByText('Session')).toBeTruthy();
    expect(screen.getByText('software-development')).toBeTruthy();
  });

  it('renders command names with slash prefix', () => {
    render(
      <SlashCommandDropdown
        groups={mockGroups}
        selectedIndex={0}
        onSelect={() => {}}
        onMouseEnterIndex={() => {}}
      />
    );
    expect(screen.getByText('/help')).toBeTruthy();
    expect(screen.getByText('/clear')).toBeTruthy();
    expect(screen.getByText('/simplify-code')).toBeTruthy();
  });

  it('renders command descriptions', () => {
    render(
      <SlashCommandDropdown
        groups={mockGroups}
        selectedIndex={0}
        onSelect={() => {}}
        onMouseEnterIndex={() => {}}
      />
    );
    expect(screen.getByText('Show available commands')).toBeTruthy();
    expect(screen.getByText('Clean up recent code changes')).toBeTruthy();
  });

  it('shows empty state when no groups', () => {
    render(
      <SlashCommandDropdown
        groups={[]}
        selectedIndex={0}
        onSelect={() => {}}
        onMouseEnterIndex={() => {}}
      />
    );
    expect(screen.getByText('No matching commands')).toBeTruthy();
  });

  it('highlights selected index', () => {
    const { container } = render(
      <SlashCommandDropdown
        groups={mockGroups}
        selectedIndex={1}
        onSelect={() => {}}
        onMouseEnterIndex={() => {}}
      />
    );
    const buttons = container.querySelectorAll('button');
    // First button (index 0) should NOT be highlighted
    expect(buttons[0].style.background).not.toContain('var(--blue)');
  });
});
