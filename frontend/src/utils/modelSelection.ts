/**
 * Model ID matching shared by Settings and the composer.
 *
 * config.yaml stores the resolved bare/slash form (``anthropic/claude-opus-4.6``)
 * while the catalog may expose the same model through a WebUI-only
 * ``@provider:`` prefix (``@nous:anthropic/claude-opus-4.6``). Compare the
 * trailing model name so a saved default still matches its catalog entry.
 *
 * Keep this in sync with `_normalize_model_id_for_match()` in
 * `backend/app/api/config.py`.
 */
export function normalizeModelId(modelId: string): string {
  let id = (modelId || '').trim().toLowerCase();
  if (id.startsWith('@')) {
    id = id.split(':').slice(1).join(':');
  }
  if (id.includes('/')) {
    id = id.split('/').slice(1).join('/');
  }
  return id.replace(/-/g, '.');
}

export function sameModelId(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  return normalizeModelId(a) === normalizeModelId(b);
}

/** Whether any ID in *ids* matches *modelId* under the normalized comparison. */
export function catalogHasModel(ids: Iterable<string>, modelId: string | null | undefined): boolean {
  if (!modelId) return false;
  const target = normalizeModelId(modelId);
  for (const id of ids) {
    if (normalizeModelId(id) === target) return true;
  }
  return false;
}
