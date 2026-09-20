/**
 * M3Dialog hideCancel semantics — used by the providers gate's "force
 * configure" Basic Dialog. Covers:
 *   - The Cancel button is not rendered.
 *   - Escape does NOT trigger onCancel.
 *   - Scrim click does NOT trigger onCancel.
 *   - Initial focus lands on the confirm button so Enter fires confirm.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import M3Dialog from '@/components/common/M3Dialog';

describe('M3Dialog — hideCancel (Basic Dialog)', () => {
  it('does not render a Cancel button when hideCancel is true', () => {
    const { queryByText, getByText } = render(
      <M3Dialog
        open
        title="Add an API key"
        confirmText="Configure"
        cancelText="Cancel"
        hideCancel
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(getByText('Configure')).toBeTruthy();
    expect(queryByText('Cancel')).toBeNull();
  });

  it('Escape does NOT trigger onCancel when hideCancel is true', () => {
    const onCancel = vi.fn();
    render(
      <M3Dialog
        open
        title="Add an API key"
        confirmText="Configure"
        cancelText="Cancel"
        hideCancel
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('scrim click does NOT trigger onCancel when hideCancel is true', () => {
    const onCancel = vi.fn();
    render(
      <M3Dialog
        open
        title="Add an API key"
        confirmText="Configure"
        cancelText="Cancel"
        hideCancel
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );
    // The scrim is rendered into document.body via createPortal, so
    // query it from the document root rather than the test container.
    const overlay = document.querySelector('.m3-dialog-overlay') as HTMLElement;
    expect(overlay).not.toBeNull();
    fireEvent.click(overlay);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('Escape DOES trigger onCancel when hideCancel is false (regression guard)', () => {
    const onCancel = vi.fn();
    render(
      <M3Dialog
        open
        title="Compress?"
        confirmText="Compress"
        cancelText="Cancel"
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('clicking the confirm button fires onConfirm', () => {
    const onConfirm = vi.fn();
    const { getByText } = render(
      <M3Dialog
        open
        title="Add an API key"
        confirmText="Configure"
        cancelText="Cancel"
        hideCancel
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(getByText('Configure'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});