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

/**
 * Strip the WebUI-only `@provider:` hint from a model id.
 *
 * The hint is an internal routing detail: it never appears in the catalog
 * ids of the active provider (those are listed bare) and it must not leak
 * into the UI text.
 */
export function stripProviderHint(modelId: string | null | undefined): string {
  const id = (modelId || '').trim();
  if (id.startsWith('@') && id.includes(':')) {
    return id.slice(id.indexOf(':') + 1);
  }
  return id;
}

/** The provider named by a model id's `@provider:` hint ('' when there is none). */
export function modelProviderHint(modelId: string | null | undefined): string {
  const id = (modelId || '').trim();
  if (id.startsWith('@') && id.includes(':')) {
    return id.slice(1, id.indexOf(':')).toLowerCase();
  }
  return '';
}

export interface CatalogGroup {
  provider_id?: string;
  models: { id: string }[];
}

/**
 * Find the catalog entry that denotes *modelId* and return its id.
 *
 * A stored session model and the catalog ids can differ in form: the catalog
 * lists the active provider's ids bare (`deepseek-flash`) while a stored
 * session model may carry the WebUI-only provider hint
 * (`@deepseek:deepseek-flash`).  The hint names the serving provider, so it
 * decides which group the model is looked up in — without it the picker
 * would show no selection at all.
 *
 * Returns null when the catalog does not offer the model.
 */
export function findCatalogModelId(
  groups: CatalogGroup[],
  modelId: string | null | undefined,
  activeProvider?: string | null,
): string | null {
  const target = (modelId || '').trim();
  if (!target) return null;
  const targetKey = stripProviderHint(target).toLowerCase();
  const matches = (id: string) => stripProviderHint(id).toLowerCase() === targetKey;

  // 1. Exact id — the stored value already is a catalog id.
  for (const g of groups) {
    for (const m of g.models) {
      if (m.id === target) return m.id;
    }
  }

  // 2. The hint names the provider — look there first.
  const hint = modelProviderHint(target);
  if (hint) {
    for (const g of groups) {
      if ((g.provider_id || '').toLowerCase() !== hint) continue;
      for (const m of g.models) {
        if (matches(m.id)) return m.id;
      }
    }
  }

  // A hinted id its provider no longer offers must not be remapped to
  // another provider's same-named model — that would mark a model the
  // conversation does not run as selected (issue #55).
  if (hint) return null;

  // 3. Bare id routes to the active provider; fall back to any group.
  const active = (activeProvider || '').toLowerCase();
  const ordered = active
    ? [...groups.filter((g) => (g.provider_id || '').toLowerCase() === active), ...groups]
    : groups;
  for (const g of ordered) {
    for (const m of g.models) {
      if (matches(m.id)) return m.id;
    }
  }
  return null;
}
