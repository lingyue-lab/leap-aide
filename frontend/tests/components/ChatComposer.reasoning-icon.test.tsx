/**
 * Pin the reasoning-effort trigger to the registered glyph so it cannot
 * silently degrade to the "?" fallback again (Issue #43).
 *
 * The trigger used the unregistered icon name "exercise", so Icon warned and
 * fell back to HelpCircle — the composer footer showed a meaningless "?".
 * The author then ruled the trigger must use "bolt" (lucide Zap, matching the
 * reasoning-effort menu items) because "psychology" collides with the
 * memory-panel empty-state illustration.
 *
 * Both Zap and the HelpCircle fallback render as SVGs, so this test asserts the
 * zap-specific lucide class (and the absence of the help-circle one) plus the
 * absence of the "Unknown icon name" dev warning at this call site.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ChatComposer from '@/components/chat/ChatComposer';
import { useSessionStore } from '@/stores/useSessionStore';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';
import { useModelsStore } from '@/stores/useModelsStore';
import type { ModelsResponse } from '@/types';

const h = vi.hoisted(() => ({
  catalog: { groups: [] } as any,
  /** Availability verdict per model id; null → "unknown". */
  availability: null as null | ((model: string) => any),
}));

vi.mock('@/services/api', async () => {
  const actual = await vi.importActual<any>('@/services/api');
  return {
    ...actual,
    modelsService: {
      getModels: vi.fn(() => Promise.resolve(h.catalog)),
      getModelAvailability: vi.fn((model: string) =>
        Promise.resolve(
          h.availability
            ? h.availability(model)
            : { model, provider: '', available: null, provider_has_key: null },
        ),
      ),
    },
    personalityService: { getAll: vi.fn(() => Promise.resolve([])) },
  };
});

vi.mock('@/components/common/ApprovalIndicator', () => ({ default: () => null }));
vi.mock('@/components/chat/ContextUsageIndicator', () => ({ default: () => null }));

/** The catalog lists the active provider's ids bare. */
const catalog: ModelsResponse = {
  groups: [
    {
      provider: 'DeepSeek',
      provider_id: 'deepseek',
      models: [
        { id: 'deepseek-flash', label: 'DeepSeek Flash' },
        { id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
      ],
    },
    {
      provider: 'Xiaomi',
      provider_id: 'xiaomi',
      models: [{ id: '@xiaomi:mimo-v2.6-flash', label: 'MiMo V2.6 Flash' }],
    },
  ],
  active_provider: 'deepseek',
  default_model: 'deepseek-flash',
  default_model_available: true,
  default_model_provider_has_key: true,
};

const session = (model: string) =>
  ({
    session_id: 'sess-1',
    title: 'Test',
    model,
    messages: [],
    created_at: 0,
  }) as any;

describe('ChatComposer — reasoning effort trigger icon', () => {
  beforeEach(() => {
    useProvidersGateStore.setState({
      configured: true,
      checkedAt: Date.now(),
      lastErrorStatus: null,
    });
    h.catalog = catalog;
    h.availability = null;
    useModelsStore.getState().cancel();
    useModelsStore.setState({ data: null, status: 'idle', error: null, fetchedAt: null });
    useSettingsStore.setState({ settings: { model: 'deepseek-flash' } as any });
    useSessionStore.setState({
      sessions: [],
      currentSessionId: 'sess-1',
      currentSession: session('@deepseek:deepseek-flash'),
      pendingNewSession: null,
    });
    global.fetch = vi.fn(() =>
      Promise.resolve(new Response('{}', { status: 200 })),
    ) as unknown as typeof fetch;
  });

  it('renders the zap (lucide Zap) glyph instead of the "?" fallback', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    const trigger = await screen.findByTitle('Reasoning Effort');
    // Zap and the HelpCircle fallback are both SVGs — assert the zap-specific
    // lucide class and the absence of the fallback's marker instead of a bare
    // "an svg exists" check.
    const zap = trigger.querySelector('svg.lucide-zap');
    expect(zap).not.toBeNull();
    expect(trigger.querySelector('svg.lucide-help-circle')).toBeNull();
    expect(
      warn.mock.calls.map((c) => String(c[0])).join('\n'),
    ).not.toContain('Unknown icon name');
    warn.mockRestore();
  });
});
