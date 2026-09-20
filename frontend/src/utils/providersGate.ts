import { showAlertDialog } from '@/components/common/DialogHost';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';
import { useSidebarPanelStore } from '@/stores/useSidebarPanelStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
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

/**
 * Open Settings → Model Providers. Shared between the gate's "Configure"
 * action and any future direct navigation callers (e.g. a "Configure
 * providers" button in an empty-state placeholder).
 *
 * Updates all three pieces of state needed for the Settings tab to
 * render correctly:
 *   1. Sidebar's activePanel (so the left-hand settings list appears
 *      alongside the main settings panel — without this, the user would
 *      see the settings detail but the sidebar would still show Chat
 *      sessions, which is jarring).
 *   2. settingsTabActive (drives NonTerminalContent in MainContent).
 *   3. selectedGroup = 'providers' (jumps straight to the providers
 *      group instead of the default 'appearance').
 */
export function openProvidersSettings(): void {
  useSidebarPanelStore.getState().setActivePanel('settings');
  useSettingsTabStore.getState().setSettingsTabActive(true);
  useSettingsTabStore.getState().setSelectedGroup('providers');
}

/**
 * Run the "no model providers configured" precondition gate.
 *
 * If `configured === true`, returns 'passed' immediately without
 * touching the dialog. If `configured === null`, performs a check first.
 * If still unconfigured, shows an M3 Basic Dialog (single primary
 * action, no Cancel button, Escape / scrim cannot dismiss) offering to
 * open Settings → Model Providers. The dialog resolves only when the user
 * clicks "Configure providers", at which point we navigate and return
 * 'navigated'.
 *
 * The dialog is shown via the module-level `showAlertDialog` helper,
 * which means this function is safe to call from non-React code as long
 * as `<DialogHost />` is mounted at the App root (which it is).
 *
 * Translations are pulled from the i18next singleton (`i18n.t`) rather
 * than `useTranslation()` because this module is called from non-React
 * contexts (event handlers, store actions, utilities). The singleton is
 * initialized once in `@/i18n/config` before any of this code runs.
 */
export async function gateProviders(): Promise<GateOutcome> {
  const t = i18n.t.bind(i18n);
  const store = useProvidersGateStore.getState();

  let configured = store.configured;
  if (configured === null) {
    configured = await store.check();
  }
  if (configured) return 'passed';

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
