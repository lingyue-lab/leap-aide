// Generates the Material Design 3 color scheme for the leap-aide seed color and
// prints `role → hex` for both light and dark modes, ready to paste into
// frontend/src/index.css.
//
// Run:  node scripts/generate-m3-theme.mjs
//
// Note: `@material/material-color-utilities` is a devDependency; the generated
// hex values are static and hardcoded into CSS, so there is NO runtime
// dependency on it.

import {
  themeFromSourceColor,
  argbFromHex,
  hexFromArgb,
} from '@material/material-color-utilities';

const SEED = '#4176E6'; // deepseek-harness brand blue (deepseek-500)

// Roles returned directly by themeFromSourceColor's Scheme.
const SCHEME_ROLES = [
  'primary', 'onPrimary', 'primaryContainer', 'onPrimaryContainer',
  'secondary', 'onSecondary', 'secondaryContainer', 'onSecondaryContainer',
  'tertiary', 'onTertiary', 'tertiaryContainer', 'onTertiaryContainer',
  'error', 'onError', 'errorContainer', 'onErrorContainer',
  'surfaceVariant', 'onSurface', 'onSurfaceVariant',
  'outline', 'outlineVariant',
  'inverseSurface', 'inverseOnSurface', 'inversePrimary',
  'scrim', 'shadow',
];

// Surface roles derived from the neutral tonal palette using the canonical M3
// tone mapping (not exposed by themeFromSourceColor in this package version).
const SURFACE_TONES = {
  light: {
    surface: 98, surfaceDim: 87, surfaceBright: 98,
    surfaceContainerLowest: 100, surfaceContainerLow: 96,
    surfaceContainer: 94, surfaceContainerHigh: 92, surfaceContainerHighest: 90,
  },
  dark: {
    surface: 6, surfaceDim: 6, surfaceBright: 24,
    surfaceContainerLowest: 4, surfaceContainerLow: 10,
    surfaceContainer: 12, surfaceContainerHigh: 17, surfaceContainerHighest: 22,
  },
};

const { schemes, palettes } = themeFromSourceColor(argbFromHex(SEED));
const up = (h) => hexFromArgb(h).toUpperCase();

for (const mode of ['light', 'dark']) {
  const scheme = schemes[mode];
  const neutral = palettes.neutral;
  const roles = {};

  for (const r of SCHEME_ROLES) {
    roles[r] = up(scheme[r]);
  }
  for (const [role, tone] of Object.entries(SURFACE_TONES[mode])) {
    roles[role] = up(neutral.tone(tone));
  }

  console.log(`/* ==== ${mode.toUpperCase()} (seed ${SEED}) ==== */`);
  for (const [k, v] of Object.entries(roles)) {
    console.log(`  --md-${k}: ${v};`);
  }
  console.log('');
}
