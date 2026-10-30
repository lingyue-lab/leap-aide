import { showAlertDialog } from '@/components/common/DialogHost';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';
import { useSidebarPanelStore } from '@/stores/useSidebarPanelStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { clearPanelTabStates } from '@/utils/panelTabs';
import i18n from '@/i18n/config';

/**
 * Outcome of a precondition gate check.
 *
 *   'passed'    — precondition satisfied; caller may proceed.
 *   'navigated' — user accepted the gate dialog and was sent to the
 *                 remediation page; caller must NOT proceed (the
 *                 dialog itself is the action).
 *
 * The providers gate uses an Alert Dialog (M3 Basic) rather than a
 * Confirmation Dialog, so there is no 'cancelled' outcome: chat / cron
 * panels are unreachable without configuring a key, so a Cancel button
 * would leave the user stuck with no way back. This is the canonical
 * return contract for `gateProviders()` and is the template for any
 * future hard precondition gate — copy this file and replace the body.
 */
export type GateOutcome = 'passed' | 'navigated';

/** Delay between retries while the server answers 503 "initializing". */
const INITIALIZING_RETRY_DELAY_MS = 1000;

/** Retry budget — the listen→ready window is ~5-7s (issue #3). */
const INITIALIZING_MAX_RETRIES = 10;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

/**
 * Open Settings → Model Providers. Shared between the gate's "Configure"
 * action and any future direct navigation callers (e.g. a "Configure
 * providers" button in an empty-state placeholder).
 *
 * Updates all the state needed for the Settings tab to render correctly:
 *   1. Every other panel flag is cleared first (`clearPanelTabStates()`).
 *      `NonTerminalContent` checks workspaces / skills / memory / tasks
 *      BEFORE settings, so leaving one armed keeps the old view in the main
 *      area while the sidebar already says Settings — the bug found in
 *      manual testing of issue #5 (Workspaces → Chat → "Configure
 *      providers").
 *   2. Sidebar's activePanel (so the left-hand settings list appears
 *      alongside the main settings panel — without this, the user would
 *      see the settings detail but the sidebar would still show Chat
 *      sessions, which is jarring).
 *   3. settingsTabActive (drives NonTerminalContent in MainContent).
 *   4. selectedGroup = 'providers' (jumps straight to the providers
 *      group instead of the default 'appearance').
 */
export function openProvidersSettings(): void {
  clearPanelTabStates();
  useSidebarPanelStore.getState().setActivePanel('settings');
  useSettingsTabStore.getState().setSettingsTabActive(true);
  useSettingsTabStore.getState().setSelectedGroup('providers');
}

/**
 * Run the "no model providers configured" precondition gate.
 *
 * Resolution order:
 *   - `configured === true`  → 'passed', no dialog, no request.
 *   - `configured === false` → show the dialog, navigate, 'navigated'.
 *   - `configured === null`  → ask the store. A 503 (server still
 *     initialising) is *not* an answer: keep asking for a bounded time.
 *     Any other failure leaves the state unknown, so the call proceeds
 *     ('passed') fail-open — without caching, which means the next entry
 *     into chat asks again. That distinction is what fixes issue #5: the
 *     first clean boot used to cache the pre-login 401 / init-503 as
 *     "configured" and the gate stayed silent.
 *
 * The dialog is shown via the module-level `showAlertDialog` helper,
 * which means this function is safe to call from non-React code as long
 * as `<DialogHost />` is mounted at the App root (which it is); the helper
 * itself waits for the host when effects flush out of order.
 *
 * Translations are pulled from the i18next singleton (`i18n.t`) rather
 * than `useTranslation()` because this module is called from non-React
 * contexts (event handlers, store actions, utilities). The singleton is
 * initialized once in `@/i18n/config` before any of this code runs.
 */
export async function gateProviders(): Promise<GateOutcome> {
  const t = i18n.t.bind(i18n);

  let configured = useProvidersGateStore.getState().configured;
  if (configured === null) {
    configured = await useProvidersGateStore.getState().check();
  }

  for (
    let attempt = 0;
    configured === null &&
    useProvidersGateStore.getState().lastErrorStatus === 503 &&
    attempt < INITIALIZING_MAX_RETRIES;
    attempt += 1
  ) {
    await sleep(INITIALIZING_RETRY_DELAY_MS);
    configured = await useProvidersGateStore.getState().check();
  }

  // Known-configured, or still unknown (network error / 5xx / still
  // initialising): let the caller proceed. Nothing was cached for the
  // unknown case, so the next entry re-checks instead of staying silent.
  if (configured !== false) return 'passed';

  await showAlertDialog({
    title: t('providers.gate.title'),
    description: t('providers.gate.description'),
    severity: 'info',
    icon: 'lock',
    confirmText: t('providers.gate.configure'),
  });

  openProvidersSettings();
  return 'navigated';
}
