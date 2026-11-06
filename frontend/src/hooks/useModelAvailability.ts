import { useEffect, useMemo, useState } from 'react';
import { modelsService } from '@/services/api';
import { useModelsStore } from '@/stores/useModelsStore';
import type { ModelAvailability } from '@/types';

/**
 * Availability of a single model id (GET /api/models/availability).
 *
 * The catalog only reports the availability of the *configured default*, so a
 * conversation that runs another model asks the backend about its own model:
 * a conversation must never silently switch models when its provider stops
 * offering them (issue #44 follow-up — an existing session used to fall back
 * to the next available model without telling the user).
 *
 * The query is repeated whenever the catalog content changes: deleting a
 * provider's API key invalidates the catalog, and the conversation's model
 * becomes unrunnable right then.  A failed query yields no verdict — the UI
 * stays quiet rather than claiming the model is broken.
 */
export function useModelAvailability(model: string): ModelAvailability | null {
  const groups = useModelsStore((s) => s.data?.groups);
  const defaultModel = useModelsStore((s) => s.data?.default_model);
  // Stable signature of the catalog content.  Polling a cold build repeats
  // the same answer, so only a real change must trigger a new query.
  const catalogSignature = useMemo(() => {
    const groupKey = (groups ?? [])
      .map((g) => `${g.provider_id ?? g.provider}:${g.models.map((m) => m.id).join(',')}`)
      .join('|');
    return `${defaultModel ?? ''}#${groupKey}`;
  }, [groups, defaultModel]);

  const [verdict, setVerdict] = useState<{
    model: string;
    availability: ModelAvailability;
  } | null>(null);

  useEffect(() => {
    const id = (model || '').trim();
    if (!id) return;
    let cancelled = false;
    modelsService
      .getModelAvailability(id)
      .then((res) => {
        if (!cancelled) setVerdict({ model: id, availability: res });
      })
      .catch(() => {
        if (!cancelled) setVerdict(null);
      });
    return () => {
      cancelled = true;
    };
  }, [model, catalogSignature]);

  // A verdict for a different model must not leak into the current one.
  return verdict && verdict.model === (model || '').trim() ? verdict.availability : null;
}
