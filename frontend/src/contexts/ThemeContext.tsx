import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

export type Theme = 'system' | 'dark' | 'light' | 'slate' | 'solarized' | 'monokai' | 'nord' | 'oled';

interface ThemeContextValue {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  effectiveTheme: 'dark' | 'light';
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: 'dark',
  setTheme: () => {},
  effectiveTheme: 'dark',
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    const stored = localStorage.getItem('hermes-theme') as Theme;
    return stored || 'dark';
  });

  const [effectiveTheme, setEffectiveTheme] = useState<'dark' | 'light'>('dark');

  const setTheme = useCallback((newTheme: Theme) => {
    setThemeState(newTheme);
    localStorage.setItem('hermes-theme', newTheme);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    let effective = theme;

    if (theme === 'system') {
      effective = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    root.setAttribute('data-theme', effective);
    setEffectiveTheme(effective === 'light' ? 'light' : 'dark');

    // Listen for system theme changes when using 'system' theme
    if (theme === 'system') {
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      const handler = (e: MediaQueryListEvent) => {
        const newEffective = e.matches ? 'dark' : 'light';
        root.setAttribute('data-theme', newEffective);
        setEffectiveTheme(newEffective === 'light' ? 'light' : 'dark');
      };
      mediaQuery.addEventListener('change', handler);
      return () => mediaQuery.removeEventListener('change', handler);
    }
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme, effectiveTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
