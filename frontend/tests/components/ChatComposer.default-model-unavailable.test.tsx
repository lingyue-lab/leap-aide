/**
 * Issue #44 — compose-footer display when the configured default model is no
 * longer offered by its provider.
 *
 * The footer must keep showing the configured value (marked unavailable)
 * instead of silently highlighting a different model, and the picker must
 * label such entries. When the default is available, no suffix is shown.
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
}));

vi.mock('@/services/api', async () => {
  const actual = await vi.importActual<any>('@/services/api');
  return {
    ...actual,
    modelsService: {
      getModels: vi.fn(() => Promise.resolve(h.catalog)),
    },
    personalityService: { getAll: vi.fn(() => Promise.resolve([])) },
  };
});

vi.mock('@/components/common/ApprovalIndicator', () => ({ default: () => null }));
vi.mock('@/components/chat/ContextUsageIndicator', () => ({ default: () => null }));

const catalog = (available: boolean): ModelsResponse => ({
  groups: [
    {
      provider: 'Anthropic',
      provider_id: 'anthropic',
      models: [
        {
          id: 'anthropic/claude-opus-4.6',
          label: 'Claude Opus 4.6',
          unavailable: !available,
        },
        { id: 'anthropic/claude-sonnet-4.6', label: 'Claude Sonnet 4.6' },
      ],
    },
  ],
  active_provider: 'anthropic',
  default_model: 'anthropic/claude-opus-4.6',
  default_model_available: available,
});

const session = (model: string) =>
  ({
    session_id: 'sess-1',
    title: 'Test',
    model,
    messages: [],
    created_at: 0,
  }) as any;

describe('ChatComposer — configured default availability', () => {
  beforeEach(() => {
    // Known-configured: gateProviders() must pass without a boot-time check &
    // without opening the "configure a provider" dialog.
    useProvidersGateStore.setState({
      configured: true,
      checkedAt: Date.now(),
      lastErrorStatus: null,
    });
    h.catalog = { groups: [] };
    useModelsStore.getState().cancel();
    useModelsStore.setState({ data: null, status: 'idle', error: null, fetchedAt: null });
    useSettingsStore.setState({
      settings: { model: 'anthropic/claude-opus-4.6' } as any,
    });
    useSessionStore.setState({
      sessions: [],
      currentSessionId: 'sess-1',
      currentSession: session('anthropic/claude-opus-4.6'),
      pendingNewSession: null,
    });
    global.fetch = vi.fn(() =>
      Promise.resolve(new Response('{}', { status: 200 })),
    ) as unknown as typeof fetch;
  });

  it('shows the configured default with an unavailable suffix', async () => {
    h.catalog = catalog(false);
    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);
    expect(
      await screen.findByTitle('claude-opus-4.6 (unavailable)'),
    ).toBeInTheDocument();
  });

  it('does not add the suffix when the default is available', async () => {
    h.catalog = catalog(true);
    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);
    const trigger = await screen.findByTitle('claude-opus-4.6');
    expect(trigger).toBeInTheDocument();
    expect(screen.queryByTitle('claude-opus-4.6 (unavailable)')).toBeNull();
  });

  it('labels unavailable entries in the model picker', async () => {
    h.catalog = catalog(false);
    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);
    fireEvent.click(await screen.findByTitle('claude-opus-4.6 (unavailable)'));
    await waitFor(() =>
      expect(document.querySelector('.selection-popover')).not.toBeNull(),
    );
    expect(screen.getByText('Claude Opus 4.6 (unavailable)')).toBeInTheDocument();
    expect(screen.getByText('Claude Sonnet 4.6')).toBeInTheDocument();
  });

  // Deleting the provider key removes the whole group, so the picker has no
  // entry for the configured default.  The footer must still show the value
  // (marked unavailable) and explain the empty picker instead of silently
  // selecting an unrelated model.
  it('shows the configured default and explains a deleted provider key', async () => {
    h.catalog = {
      groups: [],
      active_provider: 'tencent-tokenhub',
      default_model: 'hy4-preview',
      default_model_available: false,
      default_model_provider_has_key: false,
    };
    // Welcome state before the settings store has loaded: the catalog's
    // default_model is the only source of the configured value.
    useSettingsStore.setState({ settings: null });
    useSessionStore.setState({
      sessions: [],
      currentSessionId: 'sess-1',
      currentSession: session(''),
      pendingNewSession: null,
    });

    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    expect(await screen.findByTitle('hy4-preview (unavailable)')).toBeInTheDocument();
    expect(
      screen.getByText(
        "Default model unavailable: its provider's API key was removed. Re-add it in Settings.",
      ),
    ).toBeInTheDocument();
  });

  // Once the user picks a runnable model for this conversation, the warning
  // about the configured default is no longer actionable — it must disappear
  // and the footer must name the model that will actually be sent.
  it('dismisses the key-missing warning after the user picks another model', async () => {
    h.catalog = {
      groups: [
        {
          provider: 'DeepSeek',
          provider_id: 'deepseek',
          models: [{ id: 'deepseek-chat', label: 'DeepSeek Chat' }],
        },
      ],
      active_provider: 'tencent-tokenhub',
      default_model: 'hy4-preview',
      default_model_available: false,
      default_model_provider_has_key: false,
    };
    useSettingsStore.setState({ settings: null });
    useSessionStore.setState({
      sessions: [],
      currentSessionId: 'sess-1',
      currentSession: session(''),
      pendingNewSession: null,
    });

    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    const trigger = await screen.findByTitle('hy4-preview (unavailable)');
    const warningText =
      "Default model unavailable: its provider's API key was removed. Re-add it in Settings.";
    expect(screen.getByText(warningText)).toBeInTheDocument();

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(document.querySelector('.selection-popover')).not.toBeNull(),
    );
    fireEvent.click(screen.getByText('DeepSeek Chat'));

    await waitFor(() => expect(screen.queryByText(warningText)).toBeNull());
    expect(screen.getByTitle('deepseek-chat')).toBeInTheDocument();
  });
});
