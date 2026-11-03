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
