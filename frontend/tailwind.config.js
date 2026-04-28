/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: ['class', '[data-theme="dark"]', '[data-theme="slate"]', '[data-theme="solarized"]', '[data-theme="monokai"]', '[data-theme="nord"]', '[data-theme="oled"]'],
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
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['SF Mono', 'Fira Code', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        xs: ['11px', { lineHeight: '1.4' }],
        sm: ['12px', { lineHeight: '1.5' }],
        base: ['14px', { lineHeight: '1.6' }],
        lg: ['16px', { lineHeight: '1.5' }],
        xl: ['18px', { lineHeight: '1.4' }],
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
