/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        background: 'var(--bg)',
        foreground: 'var(--text)',
        sidebar: 'var(--sidebar)',
        border: 'var(--border)',
        border2: 'var(--border2)',
        muted: 'var(--muted)',
        accent: 'var(--accent)',
        blue: 'var(--blue)',
        gold: 'var(--gold)',
        surface: 'var(--surface)',
        topbar: 'var(--topbar-bg)',
        code: 'var(--code-bg)',
        primary: 'var(--md-primary)',
        onPrimary: 'var(--md-on-primary)',
        primaryContainer: 'var(--md-primary-container)',
        onSurface: 'var(--md-on-surface)',
        onSurfaceVariant: 'var(--md-on-surface-variant)',
        surfaceContainerHighest: 'var(--md-surface-container-highest)',
        outline: 'var(--md-outline)',
        outlineVariant: 'var(--md-outline-variant)',
        error: 'var(--md-error)',
        success: 'var(--md-success)',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['SF Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        xs: ['13px', { lineHeight: '1.4' }],
        sm: ['14px', { lineHeight: '1.5' }],
        base: ['16px', { lineHeight: '1.6' }],
        lg: ['18px', { lineHeight: '1.5' }],
        xl: ['21px', { lineHeight: '1.4' }],
      },
      spacing: {
        18: '4.5rem',
      },
      animation: {
        pulse: 'pulse 1.4s ease-in-out infinite',
        flash: 'flash 1.4s ease-out forwards',
      },
      keyframes: {
        pulse: {
          '0%, 80%, 100%': { opacity: '0.2', transform: 'scale(0.8)' },
          '40%': { opacity: '0.8', transform: 'scale(1)' },
        },
        flash: {
          '0%': { background: 'rgba(124, 185, 255, 0.22)', color: 'var(--blue)' },
          '100%': { background: 'transparent', color: 'var(--muted)' },
        },
      },
    },
  },
  plugins: [],
};
