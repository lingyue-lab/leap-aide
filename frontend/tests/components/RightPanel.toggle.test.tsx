/**
 * Regression: RightPanel renders the `open` class when isOpen=true, hides it
 * otherwise, and wires the close-preview button through the workspace store.
 *
 * Originally asserted in tests/test_workspace_panel_persists_on_empty_boot.py
 * (now deleted). The old test referenced the deleted `static/boot.js`
 * vanilla-JS module, which had a `syncWorkspacePanelState()` state machine
 * with `preview` / `browse` / `closed` modes. The React rewrite collapsed
 * those to a single boolean (`isOpen`) on `Layout`, and the React component
 * owns only the visual chrome (panel header, action buttons, resize handle)
 * and dispatches store actions. We port the two assertions that still
 * describe real, executable behaviour.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render } from '@testing-library/react';
import RightPanel from '@/components/layout/RightPanel';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

// Child workspace components hit /api/* on mount; stub them so the test
// exercises only RightPanel's chrome without depending on the network.
vi.mock('@/components/workspace/FileTree', () => ({ default: () => null }));
vi.mock('@/components/workspace/FilePreview', () => ({ default: () => null }));
vi.mock('@/components/workspace/BreadcrumbBar', () => ({ default: () => null }));

beforeEach(() => {
  useWorkspaceStore.setState({
    currentDir: '.',
    gitInfo: undefined,
    previewPath: null,
  });
});

describe('RightPanel — open/closed class on root <aside>', () => {
  it('adds the `open` class when isOpen=true', () => {
    render(<RightPanel isOpen={true} onClose={() => {}} />);
    const aside = document.querySelector('aside.right-panel');
    expect(aside).not.toBeNull();
    expect(aside!.className).toContain('open');
  });

  it('omits the `open` class when isOpen=false', () => {
    render(<RightPanel isOpen={false} onClose={() => {}} />);
    const aside = document.querySelector('aside.right-panel');
    expect(aside).not.toBeNull();
    expect(aside!.className).not.toContain('open');
  });

  it('keeps the resize handle mounted in both states (so the user can drag to reopen)', () => {
    const { rerender } = render(<RightPanel isOpen={false} onClose={() => {}} />);
    expect(document.getElementById('rightpanelResize')).not.toBeNull();
    rerender(<RightPanel isOpen={true} onClose={() => {}} />);
    expect(document.getElementById('rightpanelResize')).not.toBeNull();
  });
});

describe('RightPanel — close-preview button visibility and store wiring', () => {
  it('hides the close-preview button when previewPath is null', () => {
    useWorkspaceStore.setState({ previewPath: null });
    render(<RightPanel isOpen={true} onClose={() => {}} />);
    const closeBtn = document.querySelector('button.close-preview') as HTMLButtonElement;
    expect(closeBtn).not.toBeNull();
    expect(closeBtn.style.display).toBe('none');
  });

  it('shows the close-preview button when previewPath is set', () => {
    useWorkspaceStore.setState({ previewPath: '/some/file.md' });
    render(<RightPanel isOpen={true} onClose={() => {}} />);
    const closeBtn = document.querySelector('button.close-preview') as HTMLButtonElement;
    expect(closeBtn).not.toBeNull();
    expect(closeBtn.style.display).not.toBe('none');
  });

  it('clicking close-preview calls setPreview(null) on the workspace store', () => {
    useWorkspaceStore.setState({ previewPath: '/some/file.md' });
    render(<RightPanel isOpen={true} onClose={() => {}} />);
    const closeBtn = document.querySelector('button.close-preview') as HTMLButtonElement;
    closeBtn.click();
    expect(useWorkspaceStore.getState().previewPath).toBeNull();
  });
});

describe('RightPanel — onClose prop is wired to the hide-panel button', () => {
  it('clicking the hide-panel button invokes onClose', () => {
    let calls = 0;
    render(<RightPanel isOpen={true} onClose={() => { calls += 1; }} />);
    // First .panel-icon-btn (title="Hide panel") triggers onClose.
    const buttons = document.querySelectorAll('button.panel-icon-btn');
    expect(buttons.length).toBeGreaterThan(0);
    (buttons[0] as HTMLButtonElement).click();
    expect(calls).toBe(1);
  });
});