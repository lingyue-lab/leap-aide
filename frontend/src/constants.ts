/**
 * Font size zoom map.
 * CSS zoom is kept at 1 in medium — actual font/SVG sizes are scaled
 * at the value level so DevTools shows correct computed values.
 *
 * Anchor: medium-mode message-row body = 16px (base CSS 14px × 16/14).
 * These zoom values only handle the small/large user preference overlay.
 */
export const FONT_ZOOM_MAP: Record<string, string> = {
  small: '0.8',
  medium: '1',
  large: '1.3',
};

/** CSS --font-scale / --icon-scale values mirroring the zoom factors. */
export const FONT_SCALE_MAP: Record<string, string> = {
  small: '0.8',
  medium: '1',
  large: '1.3',
};
