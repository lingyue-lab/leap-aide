/**
 * config.yaml editor: saving through the shared EditorView must confirm with
 * the M3 snackbar (the same presentation every save action uses), not with
 * an ad-hoc inline label.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import SettingsDetail from '@/components/settings/SettingsDetail';
import Snackbar from '@/components/common/Snackbar';
import { useSettingsTabStore } from '@/stores/useSettingsTabStore';
import { configFileService } from '@/services/api';

vi.mock('@/services/api', () => ({
  providersService: {
    getProviders: vi.fn(),
    setKey: vi.fn(),
    deleteKey: vi.fn(),
  },
  configFileService: {
    getRaw: vi.fn(),
    saveRaw: vi.fn(),
  },
}));

vi.mock('@/stores/useSettingsStore', () => {
  const useSettingsStore = (sel?: (s: Record<string, unknown>) => unknown) =>
    sel ? sel({ settings: null, loadSettings: () => {} }) : { settings: null, loadSettings: () => {} };
  (useSettingsStore as unknown as { getState: () => Record<string, unknown> }).getState =
    () => ({ settings: null, loadSettings: () => {} });
  return { useSettingsStore };
});

const mockedGetRaw = vi.mocked(configFileService.getRaw);
const mockedSaveRaw = vi.mocked(configFileService.saveRaw);

beforeEach(() => {
  useSettingsTabStore.setState({
    settingsTabActive: true,
    selectedGroup: 'config',
  });
  mockedGetRaw.mockReset();
  mockedSaveRaw.mockReset();
  mockedGetRaw.mockResolvedValue('model:\n  default: anthropic\n');
  mockedSaveRaw.mockResolvedValue(undefined as never);
});

describe('SettingsDetail — config.yaml save feedback', () => {
  it('shows the Saved snackbar after the config file is written', async () => {
    render(
      <>
        <SettingsDetail />
        <Snackbar />
      </>
    );

    await waitFor(() => expect(mockedGetRaw).toHaveBeenCalled());

    // Open the config editor from the header's edit affordance.
    fireEvent.click(screen.getByTitle('edit'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockedSaveRaw).toHaveBeenCalled());
    expect(await screen.findByRole('status')).toHaveTextContent('Saved');

    // Save is a checkpoint, not an exit: the toolbar stays, exactly as in the
    // workspace file editor.
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('keeps the error inline and shows no snackbar when the write fails', async () => {
    mockedSaveRaw.mockRejectedValue(new Error('Permission denied'));

    render(
      <>
        <SettingsDetail />
        <Snackbar />
      </>
    );

    await waitFor(() => expect(mockedGetRaw).toHaveBeenCalled());
    fireEvent.click(screen.getByTitle('edit'));
    fireEvent.click(await screen.findByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(screen.getByText('Permission denied')).toBeInTheDocument()
    );
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
