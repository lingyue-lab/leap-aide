/**
 * Vitest setup — runs before every test file.
 *
 * Wires up jest-dom matchers (toBeInTheDocument etc.) and stubs any
 * browser-only globals the components rely on but the test environment
 * does not provide (e.g. matchMedia for the i18n + M3 component stack).
 */
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';
import './i18nForTests';

// @testing-library/react auto-registers cleanup ONLY when an `afterEach`
// global is available at module load time AND the test file imports from
// `@testing-library/react`. Doing it explicitly here is robust against
// future changes to either library's auto-cleanup behaviour.
afterEach(() => {
  cleanup();
});

// jsdom does not implement matchMedia; components that read it (e.g. M3
// responsive breakpoints) would otherwise crash on import.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},          // deprecated API used by some libs
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// IntersectionObserver is also missing from jsdom; stub it so any
// component using framer-motion or scroll-spy hooks does not crash.
if (typeof globalThis.IntersectionObserver === 'undefined') {
  class StubIntersectionObserver {
    readonly root = null;
    readonly rootMargin = '';
    readonly thresholds = [];
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() { return []; }
  }
  // @ts-expect-error — assigning a stub class to a global missing in jsdom
  globalThis.IntersectionObserver = StubIntersectionObserver;
}

// jsdom (or vitest's jsdom env) installs a `localStorage` property on
// globalThis whose VALUE is undefined — so the property is `in` globalThis
// but reading `localStorage.getItem` throws. Bare `localStorage.getItem(...)`
// at module top level (src/i18n/config.ts et al.) explodes under vitest.
// Install an in-memory Storage stub whenever the existing one is missing,
// null, or not a working Storage. Reinstall by try/catch so non-configurable
// pre-existing slots also get fixed.
const existing = (globalThis as { localStorage?: unknown }).localStorage;
const isWorkingStorage =
  existing != null &&
  typeof (existing as Storage).getItem === 'function';
if (!isWorkingStorage) {
  const store = new Map<string, string>();
  const stub: Storage = {
    getItem: (k) => (store.has(k) ? store.get(k)! : null),
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); },
    clear: () => { store.clear(); },
    key: (i) => Array.from(store.keys())[i] ?? null,
    get length() { return store.size; },
  };
  try {
    Object.defineProperty(globalThis, 'localStorage', {
      value: stub,
      writable: true,
      configurable: true,
      enumerable: true,
    });
  } catch {
    // jsdom locked the slot as non-configurable. Direct assignment is the
    // last resort; in non-strict (default) mode it succeeds when writable.
    try {
      (globalThis as { localStorage?: Storage }).localStorage = stub;
    } catch {
      // Cannot replace. Leave the broken one in place; tests will fail loud
      // and visibly so the issue is debuggable instead of silently regressing.
    }
  }
}