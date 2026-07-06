import { useState, useEffect, useMemo, useCallback } from 'react';
import { slashCommandsService } from '@/services/api';
import type { SlashCommand } from '@/types';

export interface GroupedCommand {
  category: string;
  commands: SlashCommand[];
}

export function useSlashCommands(dynamicArgs?: Record<string, string[]>) {
  const dynArgs = dynamicArgs ?? {};
  const [commands, setCommands] = useState<SlashCommand[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    slashCommandsService
      .list()
      .then((resp) => {
        const builtins: SlashCommand[] = (resp.builtin || []).map((c) => ({
          ...c,
          type: 'builtin' as const,
          category: c.category || 'Built-in',
          subcommands: c.subcommands || [],
        }));
        const skills: SlashCommand[] = (resp.skills || []).map((c) => ({
          ...c,
          type: 'skill' as const,
          name: c.slug || c.name,
          category: c.category || 'Skills',
        }));
        const plugins: SlashCommand[] = (resp.plugins || []).map((c) => ({
          ...c,
          type: 'plugin' as const,
          category: c.category || 'Plugins',
        }));
        setCommands([...builtins, ...skills, ...plugins]);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  /** Commands grouped by category for dropdown rendering. */
  const grouped = useMemo<GroupedCommand[]>(() => {
    const map = new Map<string, SlashCommand[]>();
    for (const cmd of commands) {
      const cat = cmd.category || 'General';
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(cmd);
    }
    return Array.from(map.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([category, cmds]) => ({
        category,
        commands: cmds.sort((a, b) => a.name.localeCompare(b.name)),
      }));
  }, [commands]);

  /** Parse args_hint "[a | b | c]" into enum values, or empty for free-text. */
  const parseArgsHint = (hint?: string): string[] => {
    if (!hint) return [];
    // Match enum patterns: [status | run | pin] or [text | pause | resume]
    const m = hint.match(/^\[(.+)\]$/);
    if (!m) return [];
    return m[1].split('|').map(s => s.trim()).filter(Boolean);
  };

  /** Filter commands matching a partial slug typed after "/".
   *  Supports arg-level matching for commands with enumerated args_hint. */
  const matchCommands = useCallback(
    (partial: string): GroupedCommand[] => {
      if (!partial) return grouped;

      // Detect duplicates
      const nameCount = new Map<string, number>();
      for (const cmd of commands) {
        nameCount.set(cmd.name, (nameCount.get(cmd.name) || 0) + 1);
      }

      const lower = partial.toLowerCase();
      const spaceIdx = lower.indexOf(' ');

      // ── Arg-level completion: "/model clau" → show matching model IDs as individual items ──
      if (spaceIdx > 0) {
        const cmdName = lower.slice(0, spaceIdx);
        const argPartial = lower.slice(spaceIdx + 1);

        const result: GroupedCommand[] = [];
        for (const g of grouped) {
          const matching = g.commands.filter((c) => {
            if (c.name.toLowerCase() !== cmdName) return false;
            // Prefer dynamic args (e.g. model IDs); fall back to subcommands or args_hint
            const subs = dynArgs[c.name]
              ? dynArgs[c.name]
              : c.subcommands && c.subcommands.length > 0
                ? c.subcommands
                : parseArgsHint(c.args_hint);
            return subs.length > 0;
          });
          if (matching.length === 0) continue;

          const enriched: (SlashCommand & { displayName?: string })[] = [];
          for (const c of matching) {
            const subs = dynArgs[c.name]
              ? dynArgs[c.name]
              : c.subcommands && c.subcommands.length > 0
                ? c.subcommands
                : parseArgsHint(c.args_hint);
            const filtered = argPartial
              ? subs.filter(v => v.toLowerCase().includes(argPartial))
              : subs;

            if (dynArgs[c.name]) {
              // Dynamic args: one dropdown item per matching value
              for (const v of filtered) {
                enriched.push({ ...c, displayName: `${c.name} ${v}`, args_hint: undefined });
              }
              // If no dynamic arg matched, still show the command entry
              if (filtered.length === 0) {
                enriched.push({ ...c, displayName: `${c.name} ` });
              }
            } else {
              // Static subcommands: pipe-join into a single entry (legacy)
              enriched.push({
                ...c,
                displayName: `${c.name} ${filtered.length > 0 ? filtered.join('|') : ''}`,
              });
            }
          }
          if (enriched.length > 0) {
            result.push({ category: g.category, commands: enriched });
          }
        }
        return result;
      }

      // ── Command-name matching (existing logic) ──
      const result: GroupedCommand[] = [];

      for (const g of grouped) {
        const matching = g.commands.filter((c) =>
          c.name.toLowerCase().startsWith(lower)
        );
        if (matching.length === 0) continue;

        const enriched = matching.map((c) => {
          const dup = (nameCount.get(c.name) || 0) > 1;
          return {
            ...c,
            displayName: dup && c.category ? `${c.category}/${c.name}` : c.name,
          };
        });

        result.push({ category: g.category, commands: enriched });
      }

      return result;
    },
    [grouped, commands, dynArgs]
  );

  /** Flatten all currently matching commands into a single list for keyboard index tracking. */
  const flattenMatching = useCallback((groups: GroupedCommand[]): (SlashCommand & { displayName?: string })[] => {
    return groups.flatMap((g) => g.commands);
  }, []);

  return { commands, grouped, matchCommands, flattenMatching, loading };
}
