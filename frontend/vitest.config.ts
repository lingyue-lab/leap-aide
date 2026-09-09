/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  // Keep build/dev plugins isolated from test runs to avoid double-init of React
  // Fast Refresh. We use the @vitejs/plugin-react plugin in test mode only for
  // JSX transform — it does not start a dev server.
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    // jsdom provides a DOM for @testing-library/react; node env would break
    // any component that touches document/window at import time.
    environment: 'jsdom',

    // Auto-load jest-dom matchers (toBeInTheDocument etc.) for every test.
    setupFiles: ['./tests/setup.ts'],

    // Tests live under tests/ at the frontend root (mirrors src/ subdirs).
    include: ['tests/**/*.{test,spec}.{ts,tsx}'],

    // Node_modules and build output are always excluded.
    exclude: [
      'node_modules',
      'dist',
      '.idea',
      '.git',
      '.cache',
    ],

    // Mirror the CI-friendly defaults from scripts/run_test.sh.
    reporters: process.env.CI ? ['default', 'junit'] : ['verbose'],

    // Output junit XML into tests/.results/ when CI mode is on.
    outputFile: {
      junit: './tests/.results/junit-frontend.xml',
    },

    // Don't watch in CI; the runner controls this.
    watch: false,
  },
});