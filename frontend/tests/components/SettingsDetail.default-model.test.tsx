/**
 * Issue #44 — Settings → Agent Behavior "Default Model" control.
 *
 * Verifies:
 *  - options mirror the composer catalog and are grouped by provider;
 *  - the saved value is matched across `@provider:` / vendor-prefix forms;
 *  - a configured value missing from the catalog stays visible under "Current";
 *  - `default_model_available === false` shows the warning, never rewrites;
 *  - Save calls POST /api/default-model only when the model actually changed;
 *  - a failed default-model save aborts the rest of the settings save.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/react';
import SettingsDetail from '@/components/settings/SettingsDetail';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { useModelsStore } from '@/stores/useModelsStore';
import { modelsService, settingsService } from '@/services/api';
import type { ModelsResponse } from '@/types';

const h = vi.hoisted(() => ({
  settings: { model: 'anthropic/claude-opus-4.6', theme: 'dark', language: 'en' } as any,
  updateSettings: vi.fn(),
  loadSettings: vi.fn(),
}));

vi.mock('@/stores/useSettingsStore', () => ({
  useSettingsStore: () => ({
    settings: h.settings,
    updateSettings: h.updateSettings,
    loadSettings: h.loadSettings,
  }),
}));

vi.mock('@/services/api', () => ({
  settingsService: { setDefaultModel: vi.fn() },
  modelsService: { getModels: vi.fn() },
  providersService: {
    getProviders: vi.fn(),
    setKey: vi.fn(),
    deleteKey: vi.fn(),
  },
  configFileService: {
    getRaw: vi.fn().mockResolvedValue(''),
    saveRaw: vi.fn(),
  },
}));

const mockedSetDefaultModel = vi.mocked(settingsService.setDefaultModel);
const mockedGetModels = vi.mocked(modelsService.getModels);
const mockedUpdateSettings = h.updateSettings;
const mockedLoadSettings = h.loadSettings;

const catalog = (overrides: Partial<ModelsResponse> = {}): ModelsResponse => ({
  groups: [
    {
      provider: 'Anthropic',
      provider_id: 'anthropic',
      models: [
        { id: '@anthropic:claude-opus-4.6', label: 'Claude Opus 4.6' },
        { id: '@anthropic:claude-haiku-4.5', label: 'Claude Haiku 4.5' },
      ],
    },
    {
      provider: 'OpenAI',
      provider_id: 'openai',
      models: [{ id: '@openai:gpt-5.4', label: 'GPT-5.4' }],
    },
  ],
  active_provider: 'anthropic',
  default_model: 'anthropic/claude-opus-4.6',
  default_model_available: true,
  ...overrides,
});

function seedCatalog(data: ModelsResponse) {
  useModelsStore.setState({
    data,
    status: 'ready',
    error: null,
    fetchedAt: Date.now(),
  });
}

function defaultModelSelect(container: HTMLElement): HTMLSelectElement {
  const selects = container.querySelectorAll('select');
  expect(selects.length).toBeGreaterThan(0);
  return selects[0] as HTMLSelectElement;
}

function saveButton(container: HTMLElement): HTMLButtonElement {
  const btn = container.querySelector('.topbar-chips button');
  expect(btn).not.toBeNull();
  return btn as HTMLButtonElement;
}

describe('SettingsDetail — default model (issue #44)', () => {
  beforeEach(() => {
    h.settings = { model: 'anthropic/claude-opus-4.6', theme: 'dark', language: 'en' };
    mockedUpdateSettings.mockReset().mockResolvedValue(undefined);
    mockedLoadSettings.mockReset().mockResolvedValue(undefined);
    mockedSetDefaultModel.mockReset().mockResolvedValue({
      ok: true,
      model: 'anthropic/claude-opus-4.6',
    });
    mockedGetModels.mockReset().mockResolvedValue(catalog());
    useSettingsTabStore.setState({
      settingsTabActive: true,
      selectedGroup: 'agent',
    });
    useModelsStore.getState().cancel();
    useModelsStore.setState({ data: null, status: 'idle', error: null, fetchedAt: null });
    global.fetch = vi.fn(() =>
      Promise.resolve(new Response('{}', { status: 200 })),
    ) as unknown as typeof fetch;
  });

  it('renders options from the shared catalog grouped by provider', () => {
    seedCatalog(catalog());
    mockedGetModels.mockResolvedValue(catalog());
    const { container } = render(<SettingsDetail />);

    const select = defaultModelSelect(container);
    const groupLabels = Array.from(select.querySelectorAll('optgroup')).map(
      (g) => g.label,
    );
    expect(groupLabels).toEqual(['Anthropic', 'OpenAI']);
    const values = Array.from(select.querySelectorAll('option')).map(
      (o) => (o as HTMLOptionElement).value,
    );
    expect(values).toContain('@anthropic:claude-opus-4.6');
    expect(values).toContain('@openai:gpt-5.4');

    // config.yaml's resolved form matches the prefixed catalog entry.
    expect(select.value).toBe('@anthropic:claude-opus-4.6');
  });

  it('keeps a configured value that is missing from the catalog visible', () => {
    h.settings = { model: 'anthropic/ghost-9', theme: 'dark', language: 'en' };
    seedCatalog(
      catalog({ default_model: 'anthropic/ghost-9', default_model_available: false }),
    );
    const { container } = render(<SettingsDetail />);

    const select = defaultModelSelect(container);
    expect(select.value).toBe('anthropic/ghost-9');
    const groupLabels = Array.from(select.querySelectorAll('optgroup')).map(
      (g) => g.label,
    );
    expect(groupLabels).toContain('Current');
  });

  it('labels catalog entries flagged unavailable', () => {
    seedCatalog(
      catalog({
        groups: [
          {
            provider: 'Anthropic',
            provider_id: 'anthropic',
            models: [
              {
                id: 'anthropic/ghost-9',
                label: 'Ghost 9',
                unavailable: true,
              },
            ],
          },
        ],
      }),
    );
    const { container } = render(<SettingsDetail />);
    const text = container.textContent ?? '';
    expect(text).toContain('Ghost 9 (unavailable)');
  });

  it('warns when the configured default is no longer offered', () => {
    h.settings = { model: 'anthropic/ghost-9', theme: 'dark', language: 'en' };
    seedCatalog(
      catalog({ default_model: 'anthropic/ghost-9', default_model_available: false }),
    );
    const { container } = render(<SettingsDetail />);
    expect(container.textContent).toContain(
      'This model is no longer offered by the configured provider.',
    );
  });

  it('explains a missing provider key instead of a removed model', () => {
    h.settings = { model: 'hy4-preview', theme: 'dark', language: 'en' };
    seedCatalog(
      catalog({
        groups: [],
        active_provider: 'tencent-tokenhub',
        default_model: 'hy4-preview',
        default_model_available: false,
        default_model_provider_has_key: false,
      }),
    );
    const { container } = render(<SettingsDetail />);
    expect(container.textContent).toContain(
      "The API key for this model's provider was removed.",
    );
    expect(container.textContent).not.toContain(
      'This model is no longer offered by the configured provider.',
    );
  });

  it('does not warn when availability is unknown (null)', () => {
    seedCatalog(
      catalog({ default_model_available: null }),
    );
    const { container } = render(<SettingsDetail />);
    expect(container.textContent).not.toContain(
      'This model is no longer offered by the configured provider.',
    );
  });

  it('saves the default model when it changed, before the settings form', async () => {
    h.settings = { model: 'anthropic/claude-opus-4.6', theme: 'dark', language: 'en' };
    seedCatalog(catalog());
    mockedGetModels.mockResolvedValue(catalog());
    const { container } = render(<SettingsDetail />);

    fireEvent.change(defaultModelSelect(container), {
      target: { value: '@openai:gpt-5.4' },
    });
    fireEvent.click(saveButton(container));

    await waitFor(() => {
      expect(mockedSetDefaultModel).toHaveBeenCalledWith('@openai:gpt-5.4');
    });
    expect(mockedUpdateSettings).toHaveBeenCalledTimes(1);
    // The POST resolves before the generic settings save.
    expect(mockedSetDefaultModel.mock.invocationCallOrder[0]).toBeLessThan(
      mockedUpdateSettings.mock.invocationCallOrder[0],
    );
    await waitFor(() => expect(mockedLoadSettings).toHaveBeenCalled());
  });

  it('skips POST /api/default-model when the model is unchanged', async () => {
    seedCatalog(catalog());
    const { container } = render(<SettingsDetail />);

    fireEvent.click(saveButton(container));

    await waitFor(() => expect(mockedUpdateSettings).toHaveBeenCalledTimes(1));
    expect(mockedSetDefaultModel).not.toHaveBeenCalled();
    expect(mockedLoadSettings).not.toHaveBeenCalled();
  });

  it('aborts the save and keeps the form when the default model fails', async () => {
    seedCatalog(catalog());
    mockedSetDefaultModel.mockRejectedValueOnce(new Error('config write failed'));
    const { container } = render(<SettingsDetail />);

    fireEvent.change(defaultModelSelect(container), {
      target: { value: '@openai:gpt-5.4' },
    });
    fireEvent.click(saveButton(container));

    await waitFor(() => {
      expect(container.textContent).toContain('config write failed');
    });
    expect(mockedUpdateSettings).not.toHaveBeenCalled();
  });
});
