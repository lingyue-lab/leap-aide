import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import Snackbar from '@/components/common/Snackbar';
import { showToast } from '@/utils/toast';

describe('Snackbar — M3 transient confirmation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('announces the message politely and auto-dismisses', () => {
    render(<Snackbar />);

    act(() => {
      showToast('Saved', { duration: 4000 });
    });

    const status = screen.getByRole('status');
    expect(status.textContent).toBe('Saved');
    expect(status.className).toContain('m3-snackbar--visible');

    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(screen.getByRole('status').className).not.toContain('m3-snackbar--visible');
  });

  it('replaces the visible message when a new toast arrives', () => {
    render(<Snackbar />);

    act(() => {
      showToast('Saving…', { duration: 10000 });
      showToast('Saved', { duration: 4000 });
    });

    expect(screen.getByRole('status').textContent).toBe('Saved');
  });
});
