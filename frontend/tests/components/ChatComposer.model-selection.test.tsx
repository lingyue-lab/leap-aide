/**
 * The model picker must mark the conversation's own model as selected.
 *
 * The stored session model can differ in form from the catalog ids: the
 * catalog lists the active provider's models bare (``deepseek-flash``) while a
 * stored session model may carry the WebUI-only provider hint
 * (``@deepseek:deepseek-flash``).  The picker compares ids literally, so the
 * composer has to resolve the stored id to the catalog entry — otherwise the
 * popover opens with nothing selected and the user cannot tell which model
 * the conversation runs.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

/** The entry the picker marks as selected. */
const selectedEntry = () =>
  document.querySelector('.ss-option.active')?.textContent?.trim() ?? null;

describe('ChatComposer — model picker selection', () => {
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

  // The stored model carries the provider hint, the catalog lists the model
  // bare — the picker must still mark it as selected.
  it('marks the session model as selected when it carries a provider hint', async () => {
    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    const trigger = await screen.findByTitle('deepseek-flash');
    fireEvent.click(trigger);
    await waitFor(() =>
      expect(document.querySelector('.selection-popover')).not.toBeNull(),
    );

    expect(selectedEntry()).toContain('DeepSeek Flash');
  });

  // The provider hint is internal routing plumbing — it must not leak into
  // the composer's trigger label.
  it('shows the model name without the provider hint', async () => {
    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    expect(await screen.findByTitle('deepseek-flash')).toBeInTheDocument();
    expect(screen.queryByTitle('@deepseek:deepseek-flash')).toBeNull();
  });

  // A model of another provider keeps its hint in the catalog, so the stored
  // value matches the entry literally.
  it('marks a model of another provider as selected', async () => {
    useSessionStore.setState({
      sessions: [],
      currentSessionId: 'sess-1',
      currentSession: session('@xiaomi:mimo-v2.6-flash'),
      pendingNewSession: null,
    });

    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    const trigger = await screen.findByTitle('mimo-v2.6-flash');
    fireEvent.click(trigger);
    await waitFor(() =>
      expect(document.querySelector('.selection-popover')).not.toBeNull(),
    );

    expect(selectedEntry()).toContain('MiMo V2.6 Flash');
  });

  // A model the catalog does not offer (its provider's key was removed) has
  // no entry to mark — the picker stays empty instead of marking a wrong one.
  it('marks nothing when the catalog does not offer the model', async () => {
    h.catalog = {
      groups: [
        {
          provider: 'Xiaomi',
          provider_id: 'xiaomi',
          models: [{ id: '@xiaomi:mimo-v2.6-flash', label: 'MiMo V2.6 Flash' }],
        },
      ],
      active_provider: 'xiaomi',
      default_model: '@xiaomi:mimo-v2.6-flash',
      default_model_available: true,
      default_model_provider_has_key: true,
    };
    // The backend knows the model is gone (its provider lost the key).
    h.availability = (model: string) => ({
      model,
      provider: 'deepseek',
      available: false,
      provider_has_key: false,
    });

    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    const trigger = await screen.findByTitle('deepseek-flash (unavailable)');
    fireEvent.click(trigger);
    await waitFor(() =>
      expect(document.querySelector('.selection-popover')).not.toBeNull(),
    );

    expect(selectedEntry()).toBeNull();
  });
});
