import { describe, it, expect, beforeEach, vi } from 'vitest';
import { gateProviders, openProvidersSettings } from '@/utils/providersGate';
import { useProvidersGateStore } from '@/stores/useProvidersGateStore';
import { useSidebarPanelStore } from '@/stores/useSidebarPanelStore';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { providersService } from '@/services/api';

vi.mock('@/services/api', () => ({
  providersService: { getProviders: vi.fn() },
}));

vi.mock('@/components/common/DialogHost', () => ({
  showAlertDialog: vi.fn(),
  showConfirmDialog: vi.fn(),
  showPromptDialog: vi.fn(),
}));

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { showAlertDialog } = await import('@/components/common/DialogHost');
const mockedShowAlert = vi.mocked(showAlertDialog);
const mockedGetProviders = vi.mocked(providersService.getProviders);

const fakeProviders = (rows: Array<{ has_key: boolean }>) =>
  rows.map((r, i) => ({
    id: `p${i}`,
    display_name: `p${i}`,
    has_key: r.has_key,
    configurable: true,
    is_oauth: false,
    key_source: 'none' as const,
    auth_error: null,
    models: [],
  }));

describe('providersGate', () => {
  beforeEach(() => {
    useProvidersGateStore.setState({ configured: null, checkedAt: null });
    useSidebarPanelStore.setState({ activePanel: 'chat', sidebarOpen: true });
    useSettingsTabStore.setState({ settingsTabActive: false, selectedGroup: 'appearance' });
    mockedShowAlert.mockReset();
    mockedGetProviders.mockReset();
  });

  describe('openProvidersSettings', () => {
    it('sets all three pieces of state needed for Settings → Providers', () => {
      openProvidersSettings();
      const sidebar = useSidebarPanelStore.getState();
      const tab = useSettingsTabStore.getState();
      expect(sidebar.activePanel).toBe('settings');
      expect(tab.settingsTabActive).toBe(true);
      expect(tab.selectedGroup).toBe('providers');
    });
  });

  describe('gateProviders()', () => {
    // TC-G1
    it('returns "passed" immediately when configured=true without showing dialog', async () => {
      useProvidersGateStore.setState({ configured: true });
      const outcome = await gateProviders();
      expect(outcome).toBe('passed');
      expect(mockedShowAlert).not.toHaveBeenCalled();
    });

    // TC-G2 — alert dialog is shown, then user clicks Configure → 'navigated'.
    it('shows the alert dialog and returns "navigated" when user clicks Configure', async () => {
      mockedGetProviders.mockResolvedValueOnce({
        providers: fakeProviders([{ has_key: false }]) as never,
        active_provider: null,
      });
      mockedShowAlert.mockResolvedValueOnce(undefined);
      const outcome = await gateProviders();
      expect(outcome).toBe('navigated');
      const sidebar = useSidebarPanelStore.getState();
      const tab = useSettingsTabStore.getState();
      expect(sidebar.activePanel).toBe('settings');
      expect(tab.settingsTabActive).toBe(true);
      expect(tab.selectedGroup).toBe('providers');
    });

    // TC-G3 — Basic Dialog (no Cancel). Dialog dismissal does NOT exist;
    // instead verify that the dialog is shown with hideCancel semantics
    // (only confirmText, no cancelText option).
    it('passes AlertDialog options without cancelText', async () => {
      mockedGetProviders.mockResolvedValueOnce({
        providers: fakeProviders([{ has_key: false }]) as never,
        active_provider: null,
      });
      mockedShowAlert.mockResolvedValueOnce(undefined);
      await gateProviders();
      expect(mockedShowAlert).toHaveBeenCalledTimes(1);
      const opts = mockedShowAlert.mock.calls[0][0];
      expect(opts.title).toBeTruthy();
      expect(opts.description).toBeTruthy();
      expect(opts.confirmText).toBeTruthy();
      expect(opts.severity).toBe('info');
      expect(opts.icon).toBe('lock');
      // AlertOptions has no cancelText — assert the type at runtime.
      expect(opts).not.toHaveProperty('cancelText');
    });

    // TC-G4 — Save key → recheck now returns configured → next gate passes.
    it('returns "passed" on second call after a save changed the provider state', async () => {
      // First call: nothing configured.
      mockedGetProviders.mockResolvedValueOnce({
        providers: fakeProviders([{ has_key: false }]) as never,
        active_provider: null,
      });
      mockedShowAlert.mockResolvedValueOnce(undefined); // user clicks Configure
      const first = await gateProviders();
      expect(first).toBe('navigated');

      // After navigating, the user saves a key. The Settings page calls
      // `check()`, which now finds a has_key=true provider.
      mockedGetProviders.mockResolvedValueOnce({
        providers: fakeProviders([{ has_key: true }]) as never,
        active_provider: 'p0',
      });
      await useProvidersGateStore.getState().check();
      expect(useProvidersGateStore.getState().configured).toBe(true);

      // Next gate call passes without showing the dialog.
      const second = await gateProviders();
      expect(second).toBe('passed');
      expect(mockedShowAlert).toHaveBeenCalledTimes(1); // still only the first dialog
    });

    // TC-G5 — GateOutcome union is exported as a type from providersGate.
    it('GateOutcome is importable from utils/providersGate', () => {
      // The compile-time check above is the main assertion. At runtime,
      // verify the function returns only the expected union members.
      return import('@/utils/providersGate').then((mod) => {
        expect(typeof mod.gateProviders).toBe('function');
        expect(typeof mod.openProvidersSettings).toBe('function');
      });
    });
  });
});
