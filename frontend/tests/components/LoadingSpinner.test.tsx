import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

// react-i18next's useTranslation hook reads translations off a global
// instance.  The LoadingSpinner.tsx component uses it for the `loading`
// fallback string; mock it so the test is independent of the locale
// bundle and we can assert the prop override path explicitly.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, fallback?: string) => fallback ?? k }),
}));

import LoadingSpinner from '@/components/common/LoadingSpinner';

describe('LoadingSpinner — M3 circular progress indicator', () => {
  it('renders the SVG with a track and one animated arc', () => {
    // M3 indeterminate variant: a static faint track ring + a single
    // animated arc that pulses in length and rotates. Both share
    // cx/cy/r/stroke-width so their centerline is identical — the
    // arc visually rides the track and never drifts off.
    render(<LoadingSpinner mode="inline" />);
    const arcs = document.querySelectorAll('.m3-circular-progress__arc');
    const tracks = document.querySelectorAll('.m3-circular-progress__track');
    expect(arcs.length).toBe(1);
    expect(tracks.length).toBe(1);
    // No separate dot — the active indicator is the stroked arc itself.
    expect(document.querySelectorAll('.m3-circular-progress__dot').length).toBe(0);
  });

  it('arc and track share cx, cy, r, stroke-width (visually flush)', () => {
    // The arc and track must be the same circle geometrically:
    //   cx=cy=24, r=18, stroke-width=4
    // Anything else and the arc will visibly drift off the track
    // when round-capped, animated, and rotated.
    render(<LoadingSpinner mode="inline" />);
    const arc = document.querySelector('.m3-circular-progress__arc') as SVGElement;
    const track = document.querySelector('.m3-circular-progress__track') as SVGElement;
    expect(arc.getAttribute('cx')).toBe(track.getAttribute('cx'));
    expect(arc.getAttribute('cy')).toBe(track.getAttribute('cy'));
    expect(arc.getAttribute('r')).toBe('18');
    expect(track.getAttribute('r')).toBe('18');
    expect(arc.getAttribute('stroke-width')).toBe('4');
    expect(track.getAttribute('stroke-width')).toBe('4');
  });

  it('respects the size prop', () => {
    render(<LoadingSpinner mode="inline" size={64} />);
    const svg = document.querySelector('.m3-circular-progress') as SVGElement | null;
    expect(svg).not.toBeNull();
    expect(svg!.getAttribute('width')).toBe('64');
    expect(svg!.getAttribute('height')).toBe('64');
  });

  it('hides text when minimal', () => {
    render(<LoadingSpinner mode="inline" minimal />);
    expect(document.querySelector('.loading-spinner-text')).toBeNull();
  });

  it('shows text when text prop is provided', () => {
    render(<LoadingSpinner mode="inline" text="Custom text" />);
    expect(screen.getByText('Custom text')).toBeTruthy();
  });

  it('omits the text element entirely when no text prop is given', () => {
    // text is truly optional: omitting the prop must not fall back to
    // a default visible "Loading…" — only the spinner should render.
    // Other fullscreen/overlay scenarios (e.g. session-switching,
    // pin/unpin, browser hard-refresh) rely on this so all loading
    // UIs look uniform.
    render(<LoadingSpinner mode="inline" />);
    expect(document.querySelector('.loading-spinner-text')).toBeNull();
    render(<LoadingSpinner mode="fullscreen" />);
    expect(document.querySelector('.loading-spinner-fullscreen-text')).toBeNull();
  });

  it('fullscreen mode is just scrim + spinner, no card wrapper', () => {
    // M3 progress indicator overlay spec: scrim + centered spinner,
    // no card / border / padding. Matches inline and overlay modes
    // (spinner-only) so the three loading UIs read as the same
    // component. Regression guard against accidentally reintroducing
    // the old dialog-card styling.
    render(<LoadingSpinner mode="fullscreen" />);
    const scrim = document.querySelector('.loading-spinner-fullscreen') as HTMLElement;
    expect(scrim).not.toBeNull();
    // The old card wrapper is gone.
    expect(document.querySelector('.loading-spinner-fullscreen-card')).toBeNull();
    // The spinner is a direct child of the scrim (not wrapped).
    const spinner = scrim.querySelector('.m3-circular-progress');
    expect(spinner).not.toBeNull();
    // Scrim is the immediate parent.
    expect(spinner!.parentElement).toBe(scrim);
  });

  it('uses M3-aligned default sizes: 24 (inline), 40 (overlay), 48 (fullscreen)', () => {
    // M3 spec defines three sizes for the circular progress indicator:
    //   - 24dp compact  → inline (button labels, list rows, dense UI)
    //   - 40dp standard → overlay (loading scrim over content)
    //   - 48dp hero     → fullscreen (blocking operations)
    // Defaults pick the matching M3 size per mode so every spinner
    // matches its context, and the three modes are spaced in a way
    // that reads as a coherent size system, not three arbitrary values.
    const { rerender } = render(<LoadingSpinner mode="inline" />);
    expect(
      (document.querySelector('.m3-circular-progress') as SVGElement | null)
        ?.getAttribute('width'),
    ).toBe('24');

    rerender(<LoadingSpinner mode="overlay" />);
    expect(
      (document.querySelector('.m3-circular-progress') as SVGElement | null)
        ?.getAttribute('width'),
    ).toBe('40');

    rerender(<LoadingSpinner mode="fullscreen" />);
    expect(
      (document.querySelector('.m3-circular-progress') as SVGElement | null)
        ?.getAttribute('width'),
    ).toBe('48');
  });

  it('fullscreen mode renders a fixed-position scrim with role=status', () => {
    render(<LoadingSpinner mode="fullscreen" />);
    const scrim = document.querySelector('.loading-spinner-fullscreen') as HTMLElement | null;
    expect(scrim).not.toBeNull();
    expect(scrim!.getAttribute('role')).toBe('status');
    expect(scrim!.getAttribute('aria-busy')).toBe('true');
  });

  it('arc and track use the M3 token-driven color', () => {
    render(<LoadingSpinner mode="inline" />);
    // We can't read the computed `color` from jsdom reliably (jsdom does
    // not parse var()), but we can assert the class hookup is present.
    const arc = document.querySelector('.m3-circular-progress__arc');
    const track = document.querySelector('.m3-circular-progress__track');
    expect(arc).not.toBeNull();
    expect(track).not.toBeNull();
  });

  it('exposes aria-label for screen readers (M3 accessibility)', () => {
    // Even when no text is shown, the SVG still gets a meaningful
    // aria-label from the i18n `loading` fallback so screen readers
    // can announce the spinner state.
    render(<LoadingSpinner mode="inline" text="Saving..." />);
    const svg = document.querySelector('.m3-circular-progress');
    expect(svg!.getAttribute('aria-label')).toBe('Saving...');

    const { rerender } = render(<LoadingSpinner mode="inline" />);
    const svgNoText = document.querySelector('.m3-circular-progress') as SVGElement | null;
    expect(svgNoText).not.toBeNull();
    expect(svgNoText!.getAttribute('aria-label')).toBeTruthy();
    rerender;
  });

  it('rotation pivot is at the SVG viewBox center, not the arc\'s fill-box', () => {
    // Regression for a subtle CSS bug: setting `transform-box: fill-box`
    // on the arc made `transform-origin: 24px 24px` resolve against the
    // arc's 36x36 bounding box (so the pivot ended up at SVG (30, 30)
    // instead of (24, 24)) — the arc then orbited a wrong pivot and
    // visibly left the track. The fix is to leave `transform-box` at
    // its SVG default (`view-box`) so the origin is interpreted in the
    // SVG coordinate system.
    render(<LoadingSpinner mode="inline" />);
    const arc = document.querySelector('.m3-circular-progress__arc') as SVGElement;
    expect(arc).not.toBeNull();
    const inlineStyle = arc.getAttribute('style') || '';
    expect(inlineStyle).not.toMatch(/transform-box\s*:\s*fill-box/i);
    const computedTransformBox = window.getComputedStyle(arc).transformBox;
    expect(computedTransformBox).not.toBe('fill-box');
  });
});