/**
 * Issue #44 — compose-footer display when the configured default model is no
 * longer offered by its provider.
 *
 * The footer must keep showing the configured value (marked unavailable)
 * instead of silently highlighting a different model, and the picker must
 * label such entries. When the default is available, no suffix is shown.
 *
 * Follow-up: an existing session that runs another model must get the same
 * treatment — the composer asks the backend about that model
 * (GET /api/models/availability) instead of falling back to the next
 * available model without a word.
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
    h.availability = null;
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
        "Default model unavailable: its provider's API key is missing or was removed. Re-add it in Settings.",
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
      "Default model unavailable: its provider's API key is missing or was removed. Re-add it in Settings.";
    expect(screen.getByText(warningText)).toBeInTheDocument();

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(document.querySelector('.selection-popover')).not.toBeNull(),
    );
    fireEvent.click(screen.getByText('DeepSeek Chat'));

    await waitFor(() => expect(screen.queryByText(warningText)).toBeNull());
    expect(screen.getByTitle('deepseek-chat')).toBeInTheDocument();
  });

  // Real welcome-state shape: the placeholder was created before settings
  // loaded, so its model is the old hard-coded fallback rather than the
  // configured default.  The warning must still appear, because
  // handleWelcomeSend sends the configured default (`hy4-preview`).
  it('warns in welcome state when the placeholder model predates settings', async () => {
    h.catalog = {
      groups: [],
      active_provider: 'tencent-tokenhub',
      default_model: 'hy4-preview',
      default_model_available: false,
      default_model_provider_has_key: false,
    };
    useSettingsStore.setState({ settings: null });
    const placeholder = {
      ...session('anthropic/claude-sonnet-4-5'),
      session_id: 'new-local-aaaa',
    };
    useSessionStore.setState({
      sessions: [],
      currentSessionId: 'new-local-aaaa',
      currentSession: placeholder,
      pendingNewSession: placeholder,
    });

    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    expect(await screen.findByTitle('hy4-preview (unavailable)')).toBeInTheDocument();
    expect(
      screen.getByText(
        "Default model unavailable: its provider's API key is missing or was removed. Re-add it in Settings.",
      ),
    ).toBeInTheDocument();
  });

  // sameModelId() compares only the trailing name.  A user who explicitly
  // picks the same trailing name on a different provider has chosen a
  // different route, so the warning must clear and the footer must name it.
  it('dismisses the warning for the same tail name on another provider', async () => {
    h.catalog = {
      groups: [
        {
          provider: 'OpenRouter',
          provider_id: 'openrouter',
          models: [
            {
              id: '@openrouter:deepseek/deepseek-v4-flash',
              label: 'DeepSeek V4 Flash (OpenRouter)',
            },
          ],
        },
      ],
      active_provider: 'deepseek',
      default_model: 'deepseek-v4-flash',
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

    const trigger = await screen.findByTitle('deepseek-v4-flash (unavailable)');
    const warningText =
      "Default model unavailable: its provider's API key is missing or was removed. Re-add it in Settings.";
    expect(screen.getByText(warningText)).toBeInTheDocument();

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(document.querySelector('.selection-popover')).not.toBeNull(),
    );
    fireEvent.click(screen.getByText('DeepSeek V4 Flash (OpenRouter)'));

    await waitFor(() => expect(screen.queryByText(warningText)).toBeNull());
    // The trigger label strips the provider hint but keeps the model's own
    // namespace (issue #55 display rule).
    expect(screen.getByTitle('deepseek/deepseek-v4-flash')).toBeInTheDocument();
  });

  // ── Existing session whose own model became unrunnable ────────────────
  //
  // The catalog only reports the *configured default's* availability, so an
  // existing session that runs another model must ask the backend about
  // that model.  Before this, the composer silently highlighted the next
  // available model of another provider — the conversation looked like it
  // had switched models.

  const sessionWithOwnModel = (model: string) => {
    useSettingsStore.setState({ settings: null });
    useSessionStore.setState({
      sessions: [],
      currentSessionId: 'sess-1',
      currentSession: session(model),
      pendingNewSession: null,
    });
  };

  // The provider's key was removed: its group vanished from the catalog and
  // the configured default (an alias of the session's model) is unavailable.
  const catalogWithoutProvider = {
    groups: [
      {
        provider: 'Tencent',
        provider_id: 'tencent-tokenhub',
        models: [{ id: 'hy4-preview', label: 'HY4 Preview' }],
      },
    ],
    active_provider: 'deepseek',
    default_model: 'deepseek-flash',
    default_model_available: false,
    default_model_provider_has_key: false,
  };

  it('keeps the session model and warns when its provider lost the API key', async () => {
    h.catalog = catalogWithoutProvider;
    h.availability = () => ({
      model: 'deepseek-v4-flash',
      provider: 'deepseek',
      available: false,
      provider_has_key: false,
    });
    sessionWithOwnModel('deepseek-v4-flash');

    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    // The session's own model stays visible (marked unavailable) — it is
    // NOT replaced by the first model of another provider.
    expect(
      await screen.findByTitle('deepseek-v4-flash (unavailable)'),
    ).toBeInTheDocument();
    expect(screen.queryByTitle('hy4-preview')).toBeNull();
    expect(
      screen.getByText(
        "Current model unavailable: its provider's API key is missing or was removed. Re-add it in Settings.",
      ),
    ).toBeInTheDocument();
  });

  // The wording follows the conversation's context, not the value: an old
  // session runs the model the user picked for it, so it must never be
  // called "the default model" — even when that model happens to be the
  // configured default as well.
  it('calls an old session model "current", not "default", even when it equals the default', async () => {
    h.catalog = {
      groups: [],
      active_provider: 'deepseek',
      default_model: 'deepseek-flash',
      default_model_available: false,
      default_model_provider_has_key: false,
    };
    h.availability = () => ({
      model: 'deepseek-flash',
      provider: 'deepseek',
      available: false,
      provider_has_key: false,
    });
    sessionWithOwnModel('deepseek-flash');

    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    expect(
      await screen.findByTitle('deepseek-flash (unavailable)'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Current model unavailable: its provider's API key is missing or was removed. Re-add it in Settings.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(
        "Default model unavailable: its provider's API key is missing or was removed. Re-add it in Settings.",
      ),
    ).toBeNull();
  });

  it('explains a retired model instead of claiming a missing key', async () => {
    h.catalog = catalogWithoutProvider;
    h.availability = () => ({
      model: 'deepseek-v4-flash',
      provider: 'deepseek',
      available: false,
      provider_has_key: true,
    });
    sessionWithOwnModel('deepseek-v4-flash');

    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    expect(
      await screen.findByTitle('deepseek-v4-flash (unavailable)'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'This model is no longer offered by its provider. Pick another model to continue.',
      ),
    ).toBeInTheDocument();
  });

  it('stays quiet while the availability of the session model is unknown', async () => {
    h.catalog = catalogWithoutProvider;
    h.availability = () => ({
      model: 'deepseek-v4-flash',
      provider: 'deepseek',
      available: null,
      provider_has_key: null,
    });
    sessionWithOwnModel('deepseek-v4-flash');

    render(<ChatComposer onSend={() => {}} onCancel={() => {}} />);

    await waitFor(() =>
      expect(screen.getByTitle('deepseek-v4-flash')).toBeInTheDocument(),
    );
    expect(screen.queryByTitle('deepseek-v4-flash (unavailable)')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });
});
