/**
 * Model-ID normalization shared by Settings and the composer (issue #44).
 *
 * config.yaml stores the resolved ``vendor/model`` form while the catalog may
 * expose the same model as ``@provider:vendor/model``; the helper must treat
 * those as equal without conflating unrelated providers.
 */
import { describe, it, expect } from 'vitest';
import {
  normalizeModelId,
  sameModelId,
  catalogHasModel,
  stripProviderHint,
  modelProviderHint,
  findCatalogModelId,
} from '@/utils/modelSelection';

describe('normalizeModelId', () => {
  it.each([
    ['anthropic/claude-opus-4.6', 'claude.opus.4.6'],
    ['@nous:anthropic/claude-opus-4.6', 'claude.opus.4.6'],
    ['@anthropic:claude-opus-4-6', 'claude.opus.4.6'],
    ['claude-opus-4-6', 'claude.opus.4.6'],
    ['CLAUDE-OPUS-4.6', 'claude.opus.4.6'],
    ['  anthropic/claude-opus-4.6  ', 'claude.opus.4.6'],
    ['minimax/minimax-m2.5:free', 'minimax.m2.5:free'],
    ['', ''],
  ])('normalizes %s to %s', (input, expected) => {
    expect(normalizeModelId(input)).toBe(expected);
  });

  it('strips only the first slash segment', () => {
    expect(normalizeModelId('vendor/team/model-1')).toBe('team/model.1');
  });
});

describe('sameModelId', () => {
  it('matches across catalog prefix formats', () => {
    expect(
      sameModelId('anthropic/claude-opus-4.6', '@nous:anthropic/claude-opus-4.6'),
    ).toBe(true);
  });

  it('matches hyphen and dot spellings', () => {
    expect(sameModelId('claude-opus-4-6', 'claude-opus-4.6')).toBe(true);
  });

  it('rejects empty or missing values', () => {
    expect(sameModelId('', '')).toBe(false);
    expect(sameModelId(null, 'x')).toBe(false);
    expect(sameModelId('x', undefined)).toBe(false);
  });

  it('rejects different models', () => {
    expect(sameModelId('openai/gpt-5.4', 'anthropic/claude-opus-4.6')).toBe(false);
  });

  it('compares the trailing model name, matching the backend contract', () => {
    // The vendor prefix is routing metadata (provider comes from the catalog
    // group), so the shared contract compares the model name itself.
    expect(sameModelId('openai/gpt-5.4', 'azure/gpt-5.4')).toBe(true);
  });
});

describe('catalogHasModel', () => {
  it('finds a model across prefix formats', () => {
    expect(
      catalogHasModel(['@anthropic:claude-opus-4.6'], 'anthropic/claude-opus-4.6'),
    ).toBe(true);
  });

  it('returns false when absent or unset', () => {
    expect(catalogHasModel(['@openai:gpt-5.4'], 'claude-opus-4.6')).toBe(false);
    expect(catalogHasModel(['@openai:gpt-5.4'], '')).toBe(false);
  });
});

describe('provider hint helpers', () => {
  it('strips the hint', () => {
    expect(stripProviderHint('@deepseek:deepseek-flash')).toBe('deepseek-flash');
    expect(stripProviderHint('deepseek-flash')).toBe('deepseek-flash');
    expect(stripProviderHint('@openrouter:deepseek/deepseek-v4-flash')).toBe(
      'deepseek/deepseek-v4-flash',
    );
    expect(stripProviderHint('')).toBe('');
  });

  it('reads the hinted provider', () => {
    expect(modelProviderHint('@deepseek:deepseek-flash')).toBe('deepseek');
    expect(modelProviderHint('deepseek-flash')).toBe('');
    expect(modelProviderHint('@openrouter:openai/gpt-5.4')).toBe('openrouter');
  });
});

// The stored session model and the catalog ids can differ in form: the
// catalog lists the active provider's ids bare while a stored model may carry
// the WebUI-only provider hint.  The picker compares ids literally, so the
// composer has to resolve the stored id to the catalog entry.
describe('findCatalogModelId', () => {
  const groups = [
    {
      provider_id: 'deepseek',
      models: [{ id: 'deepseek-flash' }, { id: 'deepseek-v4-pro' }],
    },
    {
      provider_id: 'xiaomi',
      models: [{ id: '@xiaomi:mimo-v2.6-flash' }],
    },
  ];

  it('returns an exact catalog id', () => {
    expect(findCatalogModelId(groups, 'deepseek-flash', 'deepseek')).toBe(
      'deepseek-flash',
    );
    expect(
      findCatalogModelId(groups, '@xiaomi:mimo-v2.6-flash', 'deepseek'),
    ).toBe('@xiaomi:mimo-v2.6-flash');
  });

  it('maps a hinted model onto the bare entry of its provider', () => {
    expect(
      findCatalogModelId(groups, '@deepseek:deepseek-flash', 'deepseek'),
    ).toBe('deepseek-flash');
  });

  it('prefers the hinted provider over an equally named model elsewhere', () => {
    const ambiguous = [
      { provider_id: 'deepseek', models: [{ id: 'deepseek-flash' }] },
      {
        provider_id: 'other',
        models: [{ id: '@other:deepseek-flash' }],
      },
    ];
    expect(
      findCatalogModelId(ambiguous, '@other:deepseek-flash', 'deepseek'),
    ).toBe('@other:deepseek-flash');
  });

  it('falls back to the active provider for a bare model', () => {
    const ambiguous = [
      { provider_id: 'other', models: [{ id: '@other:flash' }] },
      { provider_id: 'deepseek', models: [{ id: 'flash' }] },
    ];
    expect(findCatalogModelId(ambiguous, 'flash', 'deepseek')).toBe('flash');
  });

  it('returns null when the catalog does not offer the model', () => {
    expect(findCatalogModelId(groups, 'missing-model', 'deepseek')).toBeNull();
    expect(findCatalogModelId(groups, '', 'deepseek')).toBeNull();
  });
});
