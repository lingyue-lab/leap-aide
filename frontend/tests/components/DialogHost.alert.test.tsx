/**
 * DialogHost alert path — exercises the new showAlertDialog helper used
 * by the providers gate (M3 Basic Dialog). Verifies that:
 *   - showAlertDialog is exported and resolves when the user clicks the
 *     single primary action.
 *   - The host renders an M3Dialog with hideCancel=true for alert
 *     requests, so no Cancel button is visible and Escape does not
 *     dismiss.
 *   - When the helper is called BEFORE the host is mounted (the bug
 *     that previously made the gate silently navigate to settings), it
 *     awaits host registration instead of resolving immediately.
 */
import { describe, it, expect } from 'vitest';
import { render, fireEvent, waitFor, cleanup } from '@testing-library/react';
import DialogHost, {
  showAlertDialog,
  showConfirmDialog,
} from '@/components/common/DialogHost';

describe('DialogHost — showAlertDialog (Basic Dialog)', () => {
  it('resolves when the user clicks the primary button', async () => {
    render(<DialogHost />);
    const promise = showAlertDialog({
      title: 'Add an API key',
      confirmText: 'Configure',
    });
    await waitFor(() => {
      expect(document.querySelector('.m3-dialog')).not.toBeNull();
    });
    expect(document.querySelectorAll('.m3-dialog .m3-btn').length).toBe(1);
    fireEvent.click(document.querySelector('.m3-dialog .m3-btn')!);
    await expect(promise).resolves.toBeUndefined();
    cleanup();
  });

  it('does NOT dismiss on Escape (Basic Dialog has no Cancel)', async () => {
    render(<DialogHost />);
    const promise = showAlertDialog({
      title: 'Add an API key',
      confirmText: 'Configure',
    });
    await waitFor(() => {
      expect(document.querySelector('.m3-dialog')).not.toBeNull();
    });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.querySelector('.m3-dialog')).not.toBeNull();
    fireEvent.click(document.querySelector('.m3-dialog .m3-btn')!);
    await promise;
    cleanup();
  });

  it('does NOT dismiss on scrim click', async () => {
    render(<DialogHost />);
    const promise = showAlertDialog({
      title: 'Add an API key',
      confirmText: 'Configure',
    });
    await waitFor(() => {
      expect(document.querySelector('.m3-dialog-overlay')).not.toBeNull();
    });
    fireEvent.click(document.querySelector('.m3-dialog-overlay')!);
    expect(document.querySelector('.m3-dialog')).not.toBeNull();
    fireEvent.click(document.querySelector('.m3-dialog .m3-btn')!);
    await promise;
    cleanup();
  });

  it('showConfirmDialog and showAlertDialog are independently exported', () => {
    expect(typeof showAlertDialog).toBe('function');
    expect(typeof showConfirmDialog).toBe('function');
    expect(showAlertDialog).not.toBe(showConfirmDialog);
  });

  // Regression: previously, when the gate fired from NonTerminalContent
  // before <DialogHost /> had mounted, showAlertDialog silently
  // resolved and the user was navigated straight to Settings with no
  // dialog shown. The whenDialogHostReady() await fixes this.
  it('awaits DialogHost mount instead of silently resolving', async () => {
    // 1. No DialogHost mounted yet. showAlertDialog should NOT resolve.
    const promise = showAlertDialog({
      title: 'Add an API key',
      confirmText: 'Configure',
    });
    let resolved = false;
    promise.then(() => { resolved = true; });
    await new Promise((r) => setTimeout(r, 20));
    expect(resolved).toBe(false);

    // 2. Mount DialogHost. The pending request should now be pushed and
    //    the dialog should appear.
    render(<DialogHost />);
    await waitFor(() => {
      expect(document.querySelector('.m3-dialog')).not.toBeNull();
    });
    // 3. Click confirm — the original promise should resolve now.
    fireEvent.click(document.querySelector('.m3-dialog .m3-btn')!);
    await expect(promise).resolves.toBeUndefined();
    cleanup();
  });
});